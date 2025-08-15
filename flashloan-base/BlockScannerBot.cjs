/**
 * arbitrageBot_block.cjs
 *
 * Event‑driven version of the WETH/USDC arbitrage scanner.
 * Instead of polling every X seconds, it reacts to **every new block**
 * emitted by the connected JSON‑RPC provider.  
 *
 * Flow per block:
 *   1. Fetch Uniswap‑V3 & SushiSwap‑V3 pool prices.
 *   2. Fetch Aave oracle WETH/USDC ratio.
 *   3. Compute Borrowing Advantage (BA%), DEX Spread (DS%),
 *      and totalArbsbps = (BA% + DS%) * 100.
 *   4. If totalArbsbps ≥ FLASHLOAN_THRESHOLD_BPS, call the
 *      FlashLoanArbitrageBot23 contract to attempt a flash‑loan trade.
 *   5. Log a single‑line summary of the block scan and rotate the log
 *      once it exceeds 1 MB.
 *
 * Usage:
 *   node arbitrageBot_block.cjs            # RPC URL comes from .env
 *
 * Required .env variables (same as the polling version):
 *   BASE_RPC_URL, PRIVATE_KEY, CONTRACT_ADDRESS, USDC_ADDRESS, WETH_ADDRESS,
 *   UNISWAP_FACTORY, SUSHISWAP_FACTORY, UNI_FEE_TIER, SUSHI_FEE_TIER,
 *   Base_Pool_Provider, FLASHLOAN_THRESHOLD_BPS (optional),
 *   FLASHLOAN_WETH_AMOUNT (optional, default 1 WETH)
 */

require("dotenv").config();
const { ethers } = require("ethers");
const fs = require("fs");

// ------------------ Load Flash‑loan contract ABI ------------------
const FlashLoanArtifact = require("../artifacts/Contracts/FlashLoanArbitrageBot23.sol/FlashLoanArbitrageBot23.json");

// ------------------ Provider & Signer ------------------
const provider = new ethers.JsonRpcProvider(process.env.BASE_RPC_URL);
const signer   = new ethers.Wallet(process.env.PRIVATE_KEY, provider);

// ------------------ Deployed flash‑loan contract ------------------
const FLASHLOAN_ADDRESS = process.env.CONTRACT_ADDRESS;
if (!FLASHLOAN_ADDRESS) {
  console.error("Missing CONTRACT_ADDRESS in .env");
  process.exit(1);
}
const flashLoanContract = new ethers.Contract(
  FLASHLOAN_ADDRESS,
  FlashLoanArtifact.abi,
  signer
);
console.log("📄  Using flash‑loan contract:", FLASHLOAN_ADDRESS);

// ------------------ Token & DEX configuration ------------------
const tokenA           = process.env.USDC_ADDRESS;   // USDC
const tokenB           = process.env.WETH_ADDRESS;   // WETH
const tokenADecimals   = 6;
const tokenBDecimals   = 18;

const uniFactory       = process.env.UNISWAP_FACTORY  || "0x33128a8fC17869897dcE68Ed026d694621f6FDfD";
const sushiFactory     = process.env.SUSHISWAP_FACTORY || "0xc35DADB65012eC5796536bD9864eD8773aBc74C4";

const uniFeeTier       = process.env.UNI_FEE_TIER   ? parseInt(process.env.UNI_FEE_TIER)   : 500;
const sushiFeeTier     = process.env.SUSHI_FEE_TIER ? parseInt(process.env.SUSHI_FEE_TIER) : 100;

const poolAddressesProvider = process.env.Base_Pool_Provider;
if (!poolAddressesProvider) {
  console.error("Missing Base_Pool_Provider in .env");
  process.exit(1);
}

// ------------------ Thresholds & amounts ------------------
const FLASHLOAN_THRESHOLD_BPS = parseFloat(process.env.ARB_THRESHOLD_BPS || "11");
const FLASHLOAN_WETH_AMOUNT   = ethers.parseUnits(
  process.env.FLASHLOAN_WETH_AMOUNT || "1",
  18
);

// ------------------ Minimal ABIs ------------------
const factoryAbi = [
  "function getPool(address tokenA, address tokenB, uint24 fee) external view returns (address)"
];
const poolAbi = [
  "function slot0() external view returns (uint160 sqrtPriceX96, int24 tick, uint16 observationIndex, uint16 observationCardinality, uint16 observationCardinalityNext, uint8 feeProtocol, bool unlocked)"
];
const extendedPoolAbi = [
  ...poolAbi,
  "function token0() external view returns (address)",
  "function token1() external view returns (address)"
];
const poolAddressesProviderAbi = [
  "function getPriceOracle() external view returns (address)"
];
const priceOracleGetterAbi = [
  "function getAssetPrice(address asset) external view returns (uint256)"
];

// ------------------ Logging helpers ------------------
const LOG_FILE = "arbitrage_log.txt";
function rotateLogIfNeeded() {
  try {
    if (fs.existsSync(LOG_FILE)) {
      const stats = fs.statSync(LOG_FILE);
      if (stats.size >= 1 * 1024 * 1024) { // 1 MB
        const newName = `arbitrage_log_${Date.now()}.txt`;
        fs.renameSync(LOG_FILE, newName);
        console.log(`🌀  Rotated log => ${newName}`);
      }
    }
  } catch (err) {
    console.error("Error rotating log file:", err);
  }
}

// ------------------ Price helpers ------------------
async function getPoolPrice(factoryAddr, feeTier) {
  const factory = new ethers.Contract(factoryAddr, factoryAbi, provider);
  const poolAddr = await factory.getPool(tokenA, tokenB, feeTier);
  if (!poolAddr || poolAddr === ethers.ZeroAddress) return null;

  const pool   = new ethers.Contract(poolAddr, extendedPoolAbi, provider);
  const slot0  = await pool.slot0();
  const sqrtPX = slot0[0];

  let rawPrice = (parseFloat(sqrtPX.toString()) / (2 ** 96)) ** 2; // token1/token0

  const tk0 = (await pool.token0()).toLowerCase();
  const tk1 = (await pool.token1()).toLowerCase();
  if (tk0 === tokenA.toLowerCase() && tk1 === tokenB.toLowerCase()) {
    // token0 = USDC, token1 = WETH => invert
    rawPrice = 1 / rawPrice;
  }
  const adjust = 10 ** (tokenBDecimals - tokenADecimals);
  return rawPrice * adjust; // WETH / USDC
}

async function getOracleRatio() {
  const providerContract = new ethers.Contract(poolAddressesProvider, poolAddressesProviderAbi, provider);
  const oracleAddr       = await providerContract.getPriceOracle();
  if (!oracleAddr || oracleAddr === ethers.ZeroAddress) throw new Error("Aave oracle zero address");

  const oracle      = new ethers.Contract(oracleAddr, priceOracleGetterAbi, provider);
  const usdcPriceBN = await oracle.getAssetPrice(tokenA);
  const wethPriceBN = await oracle.getAssetPrice(tokenB);
  const usdcPrice   = parseFloat(usdcPriceBN.toString());
  const wethPrice   = parseFloat(wethPriceBN.toString());
  if (usdcPrice <= 0 || wethPrice <= 0) throw new Error("Invalid oracle prices");
  return wethPrice / usdcPrice; // WETH / USDC
}

async function checkArbitrageOpportunity() {
  const uniPrice   = await getPoolPrice(uniFactory,   uniFeeTier);
  const sushiPrice = await getPoolPrice(sushiFactory, sushiFeeTier);
  if (!uniPrice || !sushiPrice) return null;

  const cheapest = Math.min(uniPrice, sushiPrice);
  const expensive = Math.max(uniPrice, sushiPrice);

  let aavePrice;
  try {
    aavePrice = await getOracleRatio();
  } catch (e) {
    console.error("Oracle fetch error:", e.message);
    return null;
  }

  const BApercent = ((cheapest - aavePrice) / cheapest) * 100;   // may be negative
  const DSpercent = ((expensive - cheapest) / cheapest) * 100;   // ≥ 0
  const totalArbsbps = (BApercent + DSpercent) * 100;            // basis‑points

  return {
    uniPrice,
    sushiPrice,
    aavePrice,
    BApercent,
    DSpercent,
    totalArbsbps
  };
}

// ------------------ Block listener logic ------------------
let opportunityCount = 0;
let flashloanCount   = 0;
let processingBlock  = false;  // simple re‑entrancy guard

async function handleBlock(blockNumber) {
  if (processingBlock) return; // skip overlapping blocks
  processingBlock = true;

  try {
    const data = await checkArbitrageOpportunity();
    const ts   = new Date().toISOString();

    if (!data) {
      const line = `${ts} | block ${blockNumber} => No pool data\n`;
      console.log(line.trim());
      fs.appendFileSync(LOG_FILE, line, "utf8");
      rotateLogIfNeeded();
      return;
    }

    opportunityCount++;
    const { uniPrice, sushiPrice, aavePrice, BApercent, DSpercent, totalArbsbps } = data;

    let route;
    if (uniPrice < sushiPrice)      route = "Buy Uni / Sell Sushi";
    else if (sushiPrice < uniPrice) route = "Buy Sushi / Sell Uni";
    else                            route = "No route advantage";

    let action = "NO_FLASHLOAN";
    if (totalArbsbps >= FLASHLOAN_THRESHOLD_BPS) {
      action = "FLASHLOAN_TRIGGERED";
      flashloanCount++;

      try {
        console.log("🚀  Flash‑loan opportunity detected (", totalArbsbps.toFixed(2), "bps ) …");
        const tx = await flashLoanContract.executeFlashLoan(FLASHLOAN_WETH_AMOUNT);
        console.log("   ↳ sent TX:", tx.hash);
        const rcpt = await tx.wait();
        console.log("   ↳ mined (status", rcpt.status, ") in block", rcpt.blockNumber);
        fs.appendFileSync(LOG_FILE, `${ts} | Flashloan TX: ${tx.hash}, status: ${rcpt.status}\n`, "utf8");
      } catch (err) {
        console.error("Flash‑loan TX error:", err.reason || err.message);
        fs.appendFileSync(LOG_FILE, `${ts} | Flashloan error: ${err}\n`, "utf8");
      }
    }

    const line =
      `${ts} | block ${blockNumber} | ` +
      `Opp #${opportunityCount} | UNI=${uniPrice.toFixed(4)} | ` +
      `SUSHI=${sushiPrice.toFixed(4)} | AAVE=${aavePrice.toFixed(4)} | ` +
      `BA=${BApercent.toFixed(4)}% | DS=${DSpercent.toFixed(4)}% | TOTALbps=${totalArbsbps.toFixed(2)} | ` +
      `ROUTE=${route} | ACTION=${action} | FLASHLOANS=${flashloanCount}\n`;

    console.log(line.trim());
    fs.appendFileSync(LOG_FILE, line, "utf8");
    rotateLogIfNeeded();
  } catch (err) {
    console.error("Monitor error:", err);
    fs.appendFileSync(LOG_FILE, `${new Date().toISOString()} | Monitor error: ${err}\n`, "utf8");
  } finally {
    processingBlock = false;
  }
}

// ------------------ Start listening ------------------
console.log("⛓️   Listening for new blocks …\n");
provider.on("block", handleBlock);

// Graceful shutdown
process.on("SIGINT", () => {
  console.log("\n👋  Stopping block listener …");
  provider.removeAllListeners("block");
  process.exit(0);
});
