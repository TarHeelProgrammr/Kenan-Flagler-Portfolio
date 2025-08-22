import os
import asyncio
import json
import logging
from logging.handlers import RotatingFileHandler
from datetime import datetime, timezone
from decimal import Decimal, getcontext
from dotenv import load_dotenv
from web3 import Web3
import websockets

# High-precision decimals
getcontext().prec = 50

# Load environment variables
load_dotenv()
RPC_URL            = os.getenv("BASE_RPC")
WS_URL             = os.getenv("BASE_WEBSOCKET")
PRIVATE_KEY        = os.getenv("PRIVATE_KEY")
FLASHSWAP_CONTRACT = Web3.to_checksum_address(os.getenv("FLASHSWAP_CONTRACT"))

# Thresholds, skip interval, and full-scan block count
THRESH_INITIAL   = Decimal(os.getenv("THRESH_INITIAL", "0.2"))
THRESH_EXECUTE   = Decimal(os.getenv("THRESH_EXECUTE", "0.8"))
SCAN_SKIP        = int(os.getenv("SCAN_SKIP", "3"))
FULL_SCAN_BLOCKS = int(os.getenv("FULL_SCAN_BLOCKS", "5"))

# Setup rotating file logger
logger = logging.getLogger("arb_scanner")
logger.setLevel(logging.INFO)
file_handler = RotatingFileHandler(
    "scanner_log.txt", maxBytes=5*1024*1024, backupCount=3, encoding="utf-8"
)
formatter = logging.Formatter("%(asctime)s | %(message)s", "%Y-%m-%dT%H:%M:%S%z")
file_handler.setFormatter(formatter)
logger.addHandler(file_handler)
console_handler = logging.StreamHandler()
console_handler.setFormatter(formatter)
logger.addHandler(console_handler)

# Web3 HTTP setup
iw3 = Web3(Web3.HTTPProvider(RPC_URL))
assert iw3.is_connected(), "HTTP provider not connected"
CHAIN_ID = iw3.eth.chain_id

# Pool addresses
P_USDC_WETH  = iw3.to_checksum_address("0xd0b53d9277642d899DF5C87A3966A349A798F224")
P_WETH_CBBTC = iw3.to_checksum_address("0x7AeA2E8A3843516afa07293a10Ac8E49906dabD1")
P_CBBTC_USDC = iw3.to_checksum_address("0xfBB6Eed8e7aa03B138556eeDaF5D271A5E1e43ef")
POOL_ADDRS   = [P_USDC_WETH, P_WETH_CBBTC, P_CBBTC_USDC]

# Token addresses
tok_usdc  = iw3.to_checksum_address("0x833589fcd6edb6e08f4c7c32d4f71b54bda02913").lower()
tok_weth  = iw3.to_checksum_address("0x4200000000000000000000000000000000000006").lower()
tok_cbbtc = iw3.to_checksum_address("0xcbb7c0000ab88b473b1f5afd9ef808440eed33bf").lower()

# Decimals and borrow amounts
TOK_DEC = {tok_usdc:6, tok_weth:18, tok_cbbtc:8}
BORROW_AMOUNTS = {tok_usdc:10_000*10**6, tok_weth:2*10**18, tok_cbbtc:1*10**8}
# Hard-coded fee tiers
FEE_TIERS = {P_USDC_WETH:500, P_WETH_CBBTC:500, P_CBBTC_USDC:500}

# Minimal Uniswap V3 Pool ABI
UNISWAP_V3_POOL_ABI = [
  {"inputs":[],"name":"slot0","outputs":[
    {"internalType":"uint160","name":"sqrtPriceX96","type":"uint160"},
    {"internalType":"int24","name":"tick","type":"int24"},
    {"internalType":"uint16","name":"obsIndex","type":"uint16"},
    {"internalType":"uint16","name":"obsCard","type":"uint16"},
    {"internalType":"uint16","name":"obsCardNext","type":"uint16"},
    {"internalType":"uint8","name":"feeProv","type":"uint8"},
    {"internalType":"bool","name":"unlocked","type":"bool"}
  ],"stateMutability":"view","type":"function"},
  {"inputs":[],"name":"token0","outputs":[{"internalType":"address","name":"","type":"address"}],"stateMutability":"view","type":"function"},
  {"inputs":[],"name":"token1","outputs":[{"internalType":"address","name":"","type":"address"}],"stateMutability":"view","type":"function"}
]

# Cache pool contracts and static info
env_pools = {}
def pool_state(addr: str):
    if addr not in env_pools:
        pool = iw3.eth.contract(address=addr, abi=UNISWAP_V3_POOL_ABI)
        t0 = pool.functions.token0().call().lower()
        t1 = pool.functions.token1().call().lower()
        env_pools[addr] = (pool, t0, t1)
    pool, t0, t1 = env_pools[addr]
    sqrtP = pool.functions.slot0().call()[0]
    fee   = FEE_TIERS[addr]
    price0to1 = (Decimal(sqrtP)**2) / Decimal(2**192)
    price = price0to1 * (Decimal(10)**(TOK_DEC[t0]-TOK_DEC[t1]))
    return t0, t1, price, fee

# Async-fetch all pool states concurrently
data_pool_states = {}
async def fetch_pool_states():
    tasks = [asyncio.to_thread(pool_state, addr) for addr in POOL_ADDRS]
    results = await asyncio.gather(*tasks)
    # map addr -> tuple(t0,t1,price,fee)
    return {addr: res for addr, res in zip(POOL_ADDRS, results)}

# Triangular paths
paths = [
  {"start":tok_usdc,  "pools":POOL_ADDRS},
  {"start":tok_weth,  "pools":[P_WETH_CBBTC,P_CBBTC_USDC,P_USDC_WETH]},
  {"start":tok_cbbtc, "pools":[P_CBBTC_USDC,P_USDC_WETH,P_WETH_CBBTC]}
]

# Compute best arbitrage using pre-fetched states with proper fee deduction
def compute_best(states_map):
    best_delta = Decimal('-Infinity')
    best_conf = None
    for p in paths:
        start = p["start"]
        for direction in (1, -1):
            seq = p["pools"] if direction==1 else list(reversed(p["pools"]))
            curr = start
            amount_out = Decimal(1)  # Start with 1 unit
            
            # Calculate the actual amount after each swap including fees
            for addr in seq:
                t0, t1, price, fee = states_map[addr]
                
                # Apply the exchange rate
                if curr == t0:
                    amount_out *= price
                    curr = t1
                else:
                    amount_out /= price
                    curr = t0
                
                # Subtract the DEX fee (fee is in basis points, e.g., 500 = 0.05%)
                fee_multiplier = Decimal(1) - (Decimal(fee) / Decimal(1_000_000))
                amount_out *= fee_multiplier
            
            # Calculate net profit percentage after all fees
            net_profit_ratio = amount_out - Decimal(1)
            delta = net_profit_ratio * Decimal(100)  # Convert to percentage
            
            if delta > best_delta:
                best_delta = delta
                best_conf = (seq, float(delta), curr)
    
    return best_conf

# Main websocket-driven loop with reconnect and ping config
async def main():
    acct = iw3.eth.account.from_key(PRIVATE_KEY)
    while True:
        try:
            async with websockets.connect(WS_URL, ping_interval=20, ping_timeout=20) as ws:
                # subscribe to newHeads
                await ws.send(json.dumps({
                    "jsonrpc":"2.0", "id":1,
                    "method":"eth_subscribe", "params":["newHeads"]
                }))
                await ws.recv()  # ack

                full = False
                remaining = 0
                count = 0

                while True:
                    msg = await ws.recv()
                    data = json.loads(msg)
                    if data.get("method") != "eth_subscription":
                        continue
                    header = data["params"]["result"]
                    block_num = int(header["number"], 16)
                    count += 1

                    if full and remaining <= 0:
                        logger.info("Finished full-scan of %d blocks, resuming every %dth block",
                                    FULL_SCAN_BLOCKS, SCAN_SKIP)
                        full = False; count = 0

                    if not full and count % SCAN_SKIP != 0:
                        continue

                    states_map = await fetch_pool_states()
                    result = compute_best(states_map)
                    
                    if result:
                        seq, delta, borrow = result
                        logger.info("Block %d | Net Δ=%.4f%% (after fees) | Route=%s", 
                                   block_num, delta, seq)

                        if not full and Decimal(delta) > THRESH_INITIAL:
                            full = True; remaining = FULL_SCAN_BLOCKS
                            logger.info("Threshold initial exceeded (Net Δ=%.4f%% after fees); scanning next %d blocks",
                                        delta, FULL_SCAN_BLOCKS)

                        if full:
                            remaining -= 1

                        if full and Decimal(delta) > THRESH_EXECUTE:
                            logger.info("Execution threshold exceeded (Net Δ=%.4f%% after fees); executing flash", delta)
                            amt = BORROW_AMOUNTS[borrow]
                            method = iw3.keccak(text="initFlash(uint256)")[:4].hex()
                            data_tx = "0x" + method + iw3.to_hex(amt)[2:].zfill(64)
                            tx = {
                                'to': FLASHSWAP_CONTRACT,
                                'data': data_tx,
                                'chainId':   CHAIN_ID,
                                'nonce':     iw3.eth.get_transaction_count(acct.address),
                                'gas':       500_000,
                                'maxPriorityFeePerGas': iw3.to_wei('2','gwei'),
                                'maxFeePerGas':         iw3.to_wei('50','gwei')
                            }
                            signed = acct.sign_transaction(tx)
                            tx_hash= iw3.eth.send_raw_transaction(signed.rawTransaction)
                            logger.info("Tx sent: %s", tx_hash.hex())
                    else:
                        logger.info("Block %d | No profitable arbitrage found", block_num)
                        
        except websockets.exceptions.ConnectionClosedError as e:
            logger.warning("WS connection closed: %s; reconnecting in 5s", e)
            await asyncio.sleep(5)
        except Exception as e:
            logger.error("Unexpected error: %s", e, exc_info=True)
            await asyncio.sleep(5)

if __name__ == "__main__":
    asyncio.run(main())


#returns most proftiable route for scanner.py 
'''
async def scan_routes():
    log.info("Pre‑fetching metadata …")
    # tokens per pool
    pool_tokens: dict[BasePool,tuple[str|None,str|None]] = {}
    for p in POOLS:
        pool_tokens[p] = await p.tokens()
    # decimals per token
    token_set = {t for toks in pool_tokens.values() for t in toks if t}
    await asyncio.gather(*[asyncio.to_thread(get_decimals,t) for t in token_set])

    pool_prices = await fetch_all_pool_prices(POOLS)
    if not pool_prices:
        log.error("No pool prices fetched – aborting")
        return

    # build candidate triangles (3 LPs covering exactly 3 distinct tokens)
    candidates = []
    for combo in combinations(POOLS,3):
        toks = set(); edges=set()
        ok=True
        for p in combo:
            t0,t1 = pool_tokens[p]
            if None in (t0,t1):
                ok=False; break
            toks.update((t0,t1)); edges.add(frozenset((t0,t1)))
        if ok and len(toks)==3 and len(edges)==3:
            candidates.append(combo)

    best_profit = Decimal("-Inf"); best_details=None

    async def process(route):
        nonlocal best_profit,best_details
        tokens = list({*pool_tokens[route[0]],*pool_tokens[route[1]],*pool_tokens[route[2]]})
        for perm in permutations(tokens,3):
            legs=[]; product=Decimal(1); eff=Decimal(1); lp_fee_pct=Decimal(0)
            for i in range(3):
                t_in, t_out = perm[i], perm[(i+1)%3]
                # find matching pool
                leg_pool=None
                for p in route:
                    if frozenset(pool_tokens[p])==frozenset((t_in,t_out)):
                        leg_pool=p; break
                if not leg_pool: break
                invert = t_in==pool_tokens[leg_pool][1]
                d0=get_decimals(pool_tokens[leg_pool][0]) or 18
                d1=get_decimals(pool_tokens[leg_pool][1]) or 18
                raw_bytes = pool_prices.get(leg_pool)
                if raw_bytes is None: break
                try:
                    raw_rate = leg_pool.decode_price(raw_bytes,invert,d0,d1)
                except Exception:
                    break
                product *= raw_rate
                ff = leg_pool._fee_factor()
                eff     *= raw_rate*ff
                lp_fee_pct += (1-ff)*100
                legs.append(leg_pool)
            else:
                gross_delta = (product-1)*100
                profit = gross_delta - (lp_fee_pct + AAVE_FEE_PCT)
                if profit>best_profit:
                    best_profit=profit
                    best_details=dict(route=legs, order=perm,
                                       gross_delta=gross_delta,
                                       profit_delta=profit,
                                       lp_fee_pct=lp_fee_pct)
    await asyncio.gather(*[process(c) for c in candidates])

    if best_details:
        pools_str = " , ".join(p.label or p.addr[:6] for p in best_details['route'])
        log.info("Δ=%.4f%% | Δ_profit=%.4f%% (dex=%.4f%% + aave=0.05%%) | [%s]",
                 best_details['gross_delta'], best_details['profit_delta'],
                 best_details['lp_fee_pct'], pools_str)
    else:
        log.info("No profitable route this block")
'''