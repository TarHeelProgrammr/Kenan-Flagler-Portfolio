// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

// ---------- AAVE V3 -----------
import { IPoolAddressesProvider } from "@aave/core-v3/contracts/interfaces/IPoolAddressesProvider.sol";
import { IPool } from "@aave/core-v3/contracts/interfaces/IPool.sol";
import { IERC20 } from "@aave/core-v3/contracts/dependencies/openzeppelin/contracts/IERC20.sol";

// ---------- Uniswap V3 & Sushi (both share ISwapRouter) -----------
import { ISwapRouter } from "@uniswap/v3-periphery/contracts/interfaces/ISwapRouter.sol";

// Minimal factory interface for price checks
interface IUniswapV3Factory {
    function getPool(address tokenA, address tokenB, uint24 fee) external view returns (address);
}

// Minimal pool interface for price checks
interface IUniswapV3Pool {
    function slot0() external view returns (
        uint160 sqrtPriceX96,
        int24  tick,
        uint16 observationIndex,
        uint16 observationCardinality,
        uint16 observationCardinalityNext,
        uint8  feeProtocol,
        bool   unlocked
    );
    function token0() external view returns (address);
    function token1() external view returns (address);
}

// Minimal Aave Oracle interface
interface IPriceOracleGetter {
    function getAssetPrice(address asset) external view returns (uint256);
}

contract FlashLoanArbitrageBot23 {
    // --- Ownership ---
    address public owner;
    modifier onlyOwner() {
        require(msg.sender == owner, "Not owner");
        _;
    }

    // --- Aave Addresses ---
    IPoolAddressesProvider public immutable provider;
    IPool public immutable pool;
    address public aaveOracle; // Price Oracle

    // --- Dex Routers ---
    ISwapRouter public immutable uniswapRouter;
    ISwapRouter public immutable sushiRouter;

    // --- Factories for price checks ---
    address public uniswapFactory;
    address public sushiswapFactory;

    // --- WETH & USDC & fee tiers ---
    address public weth;
    address public usdc;
    uint8  public wethDecimals;
    uint8  public usdcDecimals;
    uint24 public uniFeeTier;
    uint24 public sushiFeeTier;

    // Arbitrage threshold in basis points (e.g. 30 => 0.30%)
    uint256 public arbThresholdBps;

    // ---------- Events ----------
    event FlashLoanExecuted(address asset, uint256 amount, uint256 premium);
    event ArbitrageProfit(uint256 profit);
    event LoanRepaid(address asset, uint256 totalRepayment);

    constructor(
        address providerAddress,
        address uniswapRouterAddress,
        address sushiRouterAddress,
        address _uniswapFactory,
        address _sushiswapFactory,
        address _weth,
        address _usdc,
        uint8 _wethDecimals,
        uint8 _usdcDecimals,
        uint24 _uniFeeTier,
        uint24 _sushiFeeTier,
        uint256 _arbThresholdBps
    ) {
        owner = msg.sender;

        // Aave
        provider = IPoolAddressesProvider(providerAddress);
        address aavePool = provider.getPool();
        require(aavePool != address(0), "Aave getPool returned zero");
        pool = IPool(aavePool);

        aaveOracle = provider.getPriceOracle();
        require(aaveOracle != address(0), "Aave oracle is zero");

        // Dex Routers
        uniswapRouter = ISwapRouter(uniswapRouterAddress);
        sushiRouter   = ISwapRouter(sushiRouterAddress);

        // Factories
        uniswapFactory   = _uniswapFactory;
        sushiswapFactory = _sushiswapFactory;

        // Token config
        weth         = _weth;
        usdc         = _usdc;
        wethDecimals = _wethDecimals;
        usdcDecimals = _usdcDecimals;
        uniFeeTier   = _uniFeeTier;
        sushiFeeTier = _sushiFeeTier;
        arbThresholdBps = _arbThresholdBps;
    }

    // --------------------------------------------------
    // 1) Public function => called by arbitrageBot.cjs
    // --------------------------------------------------
    function executeFlashLoan(uint256 flashAmount) external onlyOwner {
        // Optional: on-chain check
        (bool hasOpp, ) = checkArbOpportunity();
        require(hasOpp, "No arbitrage found on-chain");

        // Borrow WETH
        pool.flashLoanSimple(
            address(this),
            weth,
            flashAmount,
            bytes(""), // no extra data
            0
        );
    }

    // --------------------------------------------------
    // 2) Aave callback => do the arbitrage trades
    // --------------------------------------------------
    function executeOperation(
        address asset,
        uint256 amount,
        uint256 premium,
        address initiator,
        bytes calldata
    ) external returns (bool) {
        require(msg.sender == address(pool), "Caller must be Aave pool");
        require(initiator == address(this),  "Initiator must be this contract");
        require(asset == weth, "Only WETH flashloan");

        emit FlashLoanExecuted(asset, amount, premium);

        // Compare Dex prices => decide route
        uint256 uniPrice   = _getPoolPrice(uniswapFactory,   uniFeeTier,   weth, usdc, wethDecimals, usdcDecimals);
        uint256 sushiPrice = _getPoolPrice(sushiswapFactory, sushiFeeTier, weth, usdc, wethDecimals, usdcDecimals);

        bool buyOnUniFirst = (uniPrice < sushiPrice);

        if (buyOnUniFirst) {
            // Step 1: WETH->USDC on Uni
            _swapExactInputSingle(weth, usdc, uniFeeTier, amount, address(uniswapRouter));
            // Step 2: USDC->WETH on Sushi
            uint256 usdcBal = IERC20(usdc).balanceOf(address(this));
            _swapExactInputSingle(usdc, weth, sushiFeeTier, usdcBal, address(sushiRouter));
        } else {
            // Step 1: WETH->USDC on Sushi
            _swapExactInputSingle(weth, usdc, sushiFeeTier, amount, address(sushiRouter));
            // Step 2: USDC->WETH on Uni
            uint256 usdcBal = IERC20(usdc).balanceOf(address(this));
            _swapExactInputSingle(usdc, weth, uniFeeTier, usdcBal, address(uniswapRouter));
        }

        // Final WETH balance
        uint256 finalWethBal = IERC20(weth).balanceOf(address(this));
        uint256 totalRepay = amount + premium;
        require(finalWethBal > totalRepay, "No arb profit");

        uint256 profit = finalWethBal - totalRepay;
        emit ArbitrageProfit(profit);

        // Repay flashloan
        IERC20(weth).approve(address(pool), totalRepay);
        emit LoanRepaid(asset, totalRepay);

        return true;
    }

    // --------------------------------------------------
    // 3) On-chain check => parallels your JS approach
    // --------------------------------------------------
    function checkArbOpportunity() public view returns (bool, uint256) {
        uint256 uniPrice   = _getPoolPrice(uniswapFactory,   uniFeeTier,   weth, usdc, wethDecimals, usdcDecimals);
        uint256 sushiPrice = _getPoolPrice(sushiswapFactory, sushiFeeTier, weth, usdc, wethDecimals, usdcDecimals);

        if (uniPrice == 0 || sushiPrice == 0) {
            return (false, 0); // no valid pool
        }

        uint256 cheapestDexPrice = (uniPrice < sushiPrice) ? uniPrice : sushiPrice;
        uint256 expensiveDexPrice = (uniPrice > sushiPrice) ? uniPrice : sushiPrice;

        uint256 aavePrice = _getAavePrice(weth, usdc);
        if (aavePrice == 0) {
            return (false, 0); // no oracle price
        }

        // Borrowing Advantage => abs( (cheapestDex - aavePrice) / cheapestDex ) * 100 => then *100 => bps
        uint256 BA_inBps = _absDiff(cheapestDexPrice, aavePrice) * 10000 / cheapestDexPrice;

        // Dex Spread => (expensive - cheapest)/cheapest * 100 => then *100 => bps
        uint256 DS_inBps = (expensiveDexPrice - cheapestDexPrice) * 10000 / cheapestDexPrice;

        uint256 totalArbsbps = BA_inBps + DS_inBps;
        bool hasOpp = (totalArbsbps >= arbThresholdBps);

        return (hasOpp, totalArbsbps);
    }

    // --------------------------------------------------
    // 4) Dex Price => same logic as your cjs
    // --------------------------------------------------
    function _getPoolPrice(
        address factoryAddr,
        uint24 fee,
        address tokenA,
        address tokenB,
        uint8 tokenADecimals,
        uint8 tokenBDecimals
    ) internal view returns (uint256) {
        address poolAddr = IUniswapV3Factory(factoryAddr).getPool(tokenA, tokenB, fee);
        if (poolAddr == address(0)) {
            return 0;
        }

        (uint160 sqrtPriceX96, , , , , , ) = IUniswapV3Pool(poolAddr).slot0();
        uint256 rawPrice = (uint256(sqrtPriceX96) * uint256(sqrtPriceX96)) >> 192;

        // Invert if token0 != tokenA
        address t0 = IUniswapV3Pool(poolAddr).token0();
        if (t0 != tokenA) {
            if (rawPrice == 0) return 0;
            rawPrice = 1e36 / rawPrice;
        }

        // decimals fix
        if (tokenBDecimals > tokenADecimals) {
            rawPrice *= 10 ** (tokenBDecimals - tokenADecimals);
        } else if (tokenADecimals > tokenBDecimals) {
            rawPrice /= 10 ** (tokenADecimals - tokenBDecimals);
        }

        return rawPrice;
    }

    // --------------------------------------------------
    // 5) Aave => WETH->USDC ratio, no *1e6
    // --------------------------------------------------
    function _getAavePrice(address _weth, address _usdc) internal view returns (uint256) {
        IPriceOracleGetter oracle = IPriceOracleGetter(aaveOracle);
        uint256 wethPrice = oracle.getAssetPrice(_weth);
        uint256 usdcPrice = oracle.getAssetPrice(_usdc);
        if (usdcPrice == 0) return 0;

        // ratio = WETH / USDC
        return (wethPrice / usdcPrice);
    }

    // --------------------------------------------------
    // 6) The swap helper => handle exactInputSingle
    // --------------------------------------------------
    function _swapExactInputSingle(
        address tokenIn,
        address tokenOut,
        uint24 feeTier,
        uint256 amountIn,
        address router
    ) internal {
        IERC20(tokenIn).approve(router, amountIn);

        ISwapRouter.ExactInputSingleParams memory params = ISwapRouter.ExactInputSingleParams({
            tokenIn: tokenIn,
            tokenOut: tokenOut,
            fee: feeTier,
            recipient: address(this),
            deadline: block.timestamp,
            amountIn: amountIn,
            amountOutMinimum: 0,
            sqrtPriceLimitX96: 0
        });

        ISwapRouter(router).exactInputSingle(params);
    }

    // --------------------------------------------------
    // 7) Utility => absolute difference
    // --------------------------------------------------
    function _absDiff(uint256 a, uint256 b) internal pure returns (uint256) {
        return (a > b) ? (a - b) : (b - a);
    }

    // --------------------------------------------------
    // 8) Withdraw Functions
    // --------------------------------------------------
    function partialWithdraw(address token, uint256 amount) external onlyOwner {
        uint256 bal = IERC20(token).balanceOf(address(this));
        require(amount > 0 && amount <= bal, "Invalid amount");
        IERC20(token).transfer(owner, amount);
    }

    function withdrawAll(address token) external onlyOwner {
        uint256 bal = IERC20(token).balanceOf(address(this));
        require(bal > 0, "No tokens to withdraw");
        IERC20(token).transfer(owner, bal);
    }

    // fallback & receive
    receive() external payable {}
    fallback() external payable {}
}
