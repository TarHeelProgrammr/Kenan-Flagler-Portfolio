from __future__ import annotations

import asyncio
import json
import logging
import os
from decimal import Decimal, getcontext
from itertools import combinations, permutations
from typing import Optional

from dotenv import load_dotenv
from web3 import Web3
from web3.exceptions import ContractLogicError

# ─── Global precision for Decimal maths ──────────────────────────────────────
getcontext().prec = 50

# ─── Paths & env ─────────────────────────────────────────────────────────────
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.join(CURRENT_DIR, "..")
ENV_PATH = os.path.join(PROJECT_ROOT, ".env")
POOLS_CFG_FP = os.path.join(PROJECT_ROOT, "pools.json")
LOG_FILE_PATH = os.path.join(PROJECT_ROOT, "scanner_log.txt")

load_dotenv(ENV_PATH)

BASE_RPC = os.getenv("BASE_RPC")
BASE_WEBSOCKET = os.getenv("BASE_WEBSOCKET")
if not BASE_RPC or not BASE_WEBSOCKET:
    raise RuntimeError("BASE_RPC / BASE_WEBSOCKET missing in .env")

AAVE_FEE_PCT = Decimal("0.05")  # 0.05% flash-loan fee
MAX_PROFIT_THRESHOLD = Decimal("15.0")  # 15% max realistic profit per trade

# Token prices in USD for proper amount calculations
TOKEN_PRICES = {
    "0x4200000000000000000000000000000000000006": Decimal("2500"),  # WETH
    "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913": Decimal("1"),     # USDC
    "0xcbb7c0000ab88b473b1f5afd9ef808440eed33bf": Decimal("65000"), # cbBTC
    "0x2ae3f1ec7f1f5012cfeab0185bfc7aa3cf0dec22": Decimal("2200"),  # cbETH
    "0x532f27101965dd16442e59d40670faf5ebb142e4": Decimal("0.01"),  # BRETT
    "0x63706e401c06ac8513145b7687a14804d17f814b": Decimal("80"),    # AAVE
}

# Token symbols for display
TOKEN_SYMBOLS = {
    "0x4200000000000000000000000000000000000006": "WETH",
    "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913": "USDC",
    "0xcbb7c0000ab88b473b1f5afd9ef808440eed33bf": "cbBTC",
    "0x2ae3f1ec7f1f5012cfeab0185bfc7aa3cf0dec22": "cbETH",
    "0x532f27101965dd16442e59d40670faf5ebb142e4": "BRETT",
    "0x63706e401c06ac8513145b7687a14804d17f814b": "AAVE",
}

# More relaxed price bounds to allow for legitimate price differences
PRICE_BOUNDS = (Decimal("10") ** -15, Decimal("10") ** 15)

# ─── Minimal Logging ─────────────────────────────────────────────────────────
logging.basicConfig(level=logging.WARNING, format='%(message)s')
log = logging.getLogger("scanner")

# ─── Web3 ────────────────────────────────────────────────────────────────────
w3 = Web3(Web3.HTTPProvider(BASE_RPC))
assert w3.is_connected(), "RPC provider is unreachable"

# Method selectors
SEL_SLOT0 = "0x3850c7bd"  # Uniswap V3 slot0()
SEL_RESERVES = "0x0902f1ac"  # Uniswap V2 getReserves()

# ─── Utility functions ──────────────────────────────────────────────────────
def abbrev_addr(addr: str) -> str:
    """Abbreviate address to 10 characters."""
    return addr[:10] if len(addr) >= 10 else addr

def get_token_symbol(token_address: str) -> str:
    """Get token symbol for display."""
    checksum_addr = Web3.to_checksum_address(token_address)
    if checksum_addr in TOKEN_SYMBOLS:
        return TOKEN_SYMBOLS[checksum_addr]
    return abbrev_addr(token_address)

def log_to_file(message: str):
    """Log message to both console and file."""
    print(message)
    with open(LOG_FILE_PATH, "a", encoding="utf-8") as f:
        f.write(message + "\n")

def clear_log_file():
    """Clear the log file at the start of each run."""
    with open(LOG_FILE_PATH, "w", encoding="utf-8") as f:
        f.write("Scanner Log\n")
        f.write("=" * 50 + "\n\n")

# ─── Token decimals cache ───────────────────────────────────────────────────
ABI_DECIMALS = [
    {
        "name": "decimals",
        "outputs": [{"type": "uint8"}],
        "inputs": [],
        "stateMutability": "view",
        "type": "function",
    }
]
_token_dec_cache: dict[str, int] = {}

def get_decimals(address: str) -> int:
    """Get token decimals with caching."""
    checksum_addr = Web3.to_checksum_address(address)
    if checksum_addr in _token_dec_cache:
        return _token_dec_cache[checksum_addr]
    try:
        c = w3.eth.contract(address=checksum_addr, abi=ABI_DECIMALS)
        dec = c.functions.decimals().call()
        _token_dec_cache[checksum_addr] = int(dec)
        return int(dec)
    except Exception:
        return 18  # Default to 18 decimals

def _norm(data: bytes | str) -> str:
    """Normalize hex data."""
    return data.hex() if isinstance(data, bytes) else data

def normalize_price(raw_price: Decimal, d0: int, d1: int) -> Decimal:
    """Normalize price based on token decimals."""
    if raw_price <= 0:
        raise ValueError("Non-positive price")
    
    # Adjust for decimal differences
    factor = Decimal(10) ** (d1 - d0)
    normalized = raw_price * factor
    
    if not (PRICE_BOUNDS[0] <= normalized <= PRICE_BOUNDS[1]):
        raise ValueError(f"Price {normalized} outside bounds {PRICE_BOUNDS}")
        
    return normalized

def calculate_v3_price_from_sqrt(sqrt_price_x96: int) -> Decimal:
    """Calculate actual price from sqrtPriceX96."""
    if sqrt_price_x96 == 0:
        raise ValueError("Zero sqrtPriceX96")
    
    # Convert to Decimal for precision
    sqrt_price = Decimal(sqrt_price_x96)
    
    # Price = (sqrtPriceX96 / 2^96)^2
    # This gives us the price of token1 in terms of token0
    price = (sqrt_price / Decimal(2**96)) ** 2
    
    return price

def validate_reserves(r0: int, r1: int, addr: str) -> None:
    """Validate pool reserves are reasonable."""
    if r0 == 0 or r1 == 0:
        raise ValueError(f"Zero reserves in {addr[:8]}")
    if r0 > 10**35 or r1 > 10**35:
        raise ValueError(f"Unrealistic reserves in {addr[:8]}: {r0}, {r1}")

# ─── Pool wrappers ───────────────────────────────────────────────────────────
class BasePool:
    def __init__(self, addr: str, fee_ppm: Optional[int], hint: list[str] | None = None):
        self.addr = Web3.to_checksum_address(addr)
        self.fee_ppm = fee_ppm
        self.dynamic_fee = False
        self._tokens: tuple[str, str] | None = (
            tuple(Web3.to_checksum_address(h).lower() for h in hint) if hint else None
        )
        self.label: str | None = None

    async def tokens(self) -> tuple[str, str]:
        """Get pool tokens with caching."""
        if self._tokens:
            return self._tokens
        # Try token0/token1
        mini_abi = [
            {
                "name": "token0",
                "outputs": [{"type": "address"}],
                "inputs": [],
                "stateMutability": "view",
                "type": "function",
            },
            {
                "name": "token1",
                "outputs": [{"type": "address"}],
                "inputs": [],
                "stateMutability": "view",
                "type": "function",
            },
        ]
        c = w3.eth.contract(address=self.addr, abi=mini_abi)
        try:
            t0, t1 = await asyncio.gather(
                asyncio.to_thread(c.functions.token0().call),
                asyncio.to_thread(c.functions.token1().call),
            )
            self._tokens = (t0.lower(), t1.lower())
            return self._tokens
        except Exception as ex:
            raise

    def _fee_factor(self) -> Decimal:
        """Get fee factor (1 - fee%)."""
        if self.dynamic_fee:
            try:
                fee_abi = [
                    {
                        "name": "fee",
                        "outputs": [{"type": "uint256"}],
                        "inputs": [],
                        "stateMutability": "view",
                        "type": "function",
                    }
                ]
                c = w3.eth.contract(address=self.addr, abi=fee_abi)
                fee_ppm = c.functions.fee().call()
            except Exception:
                fee_ppm = self.fee_ppm or 0
        else:
            fee_ppm = self.fee_ppm or 0
        
        fee_factor = Decimal(1) - Decimal(fee_ppm) / Decimal(1_000_000)
        return fee_factor

    def build_call(self) -> dict[str, str]:
        raise NotImplementedError

    def decode_price(self, data: str, invert: bool, d0: int, d1: int) -> Decimal:
        raise NotImplementedError

class V3Pool(BasePool):
    def build_call(self):
        return {"to": self.addr, "data": SEL_SLOT0}

    def decode_price(self, data, invert, d0, d1):
        try:
            data = _norm(data)
            sqrt_price_x96 = int(data[2:66], 16)
            if sqrt_price_x96 == 0:
                raise ValueError(f"Zero sqrtPriceX96 in {self.addr[:8]}")
            
            # Calculate the actual price
            raw_price = calculate_v3_price_from_sqrt(sqrt_price_x96)
            
            # Apply inversion if needed
            if invert:
                raw_price = Decimal(1) / raw_price
                
            result = normalize_price(raw_price, d0, d1)
            return result
        except Exception as ex:
            raise

class V2Pair(BasePool):
    def __init__(self, addr, fee_ppm, hint=None):
        super().__init__(addr, fee_ppm or 3_000, hint)

    def build_call(self):
        return {"to": self.addr, "data": SEL_RESERVES}

    def decode_price(self, data, invert, d0, d1):
        try:
            data = _norm(data)
            r0 = int(data[2:66], 16)
            r1 = int(data[66:130], 16)
            validate_reserves(r0, r1, self.addr)
            
            # Price = reserve1 / reserve0 (token1 per token0)
            raw_price = Decimal(r1) / Decimal(r0)
            
            if invert:
                raw_price = Decimal(1) / raw_price
                
            result = normalize_price(raw_price, d0, d1)
            return result
        except Exception as ex:
            raise

class AerodromeStaticPool(BasePool):
    def build_call(self):
        return {"to": self.addr, "data": SEL_RESERVES}

    def decode_price(self, data, invert, d0, d1):
        try:
            data = _norm(data)
            r0 = int(data[2:66], 16)
            r1 = int(data[66:130], 16)
            validate_reserves(r0, r1, self.addr)
            
            raw_price = Decimal(r1) / Decimal(r0)
            if invert:
                raw_price = Decimal(1) / raw_price
                
            result = normalize_price(raw_price, d0, d1)
            return result
        except Exception as ex:
            raise

# Registry
PROTO = {
    "uniswap_v3": V3Pool,
    "sushiswap_v3": V3Pool,
    "pancakeswap_v3": V3Pool,
    "uniswap_v2": V2Pair,
    "aerodrome_static": AerodromeStaticPool,
}

# ─── Load pools.json ─────────────────────────────────────────────────────────
with open(POOLS_CFG_FP, "r", encoding="utf-8") as fp:
    pools_cfg = json.load(fp)

POOLS: list[BasePool] = []
for cfg in pools_cfg:
    proto_cls = PROTO.get(cfg["protocol"].lower())
    if not proto_cls:
        continue
    
    pool = proto_cls(cfg["address"], cfg.get("fee"), cfg.get("tokens"))
    pool.label = cfg.get("label")
    pool.dynamic_fee = cfg.get("dynamic_fee", False)
    POOLS.append(pool)

print(f"Loaded {len(POOLS)} pools")

async def fetch_all_pool_prices(pools: list[BasePool]):
    """Return mapping {pool: raw_bytes}. Pools that revert are omitted."""
    
    async def _one(pool: BasePool):
        try:
            call = pool.build_call()
            result = await asyncio.to_thread(w3.eth.call, call)
            return result
        except Exception:
            return None
    
    results = await asyncio.gather(*[_one(p) for p in pools])
    successful_pools = {p: d for p, d in zip(pools, results) if d is not None}
    failed_pools = [p for p, d in zip(pools, results) if d is None]
    
    # Log successful pools
    log_message = f"Successfully fetched prices for {len(successful_pools)}/{len(pools)} pools"
    log_to_file(log_message)
    log_to_file("\nSuccessful pools:")
    log_to_file("-" * 40)
    
    for pool in successful_pools.keys():
        pool_info = f"{abbrev_addr(pool.addr)} | {pool.label or 'No label'}"
        log_to_file(pool_info)
    
    # Log failed pools if any
    if failed_pools:
        log_to_file(f"\nFailed to fetch data from {len(failed_pools)} pools:")
        log_to_file("-" * 40)
        for pool in failed_pools:
            pool_info = f"{abbrev_addr(pool.addr)} | {pool.label or 'No label'}"
            log_to_file(pool_info)
    
    log_to_file("")  # Add blank line
    
    return successful_pools

async def scan_routes():
    """Scan all arbitrage routes and return profitable opportunities."""
    log_to_file("Starting arbitrage route scanning...")
    
    # Get tokens per pool
    pool_tokens: dict[BasePool, tuple[str, str]] = {}
    for p in POOLS:
        try:
            tokens = await p.tokens()
            pool_tokens[p] = tokens
        except Exception:
            continue

    log_to_file(f"Successfully retrieved tokens for {len(pool_tokens)} pools")

    # Get unique tokens and their decimals
    token_set = {t for toks in pool_tokens.values() for t in toks}
    log_to_file(f"Found {len(token_set)} unique tokens")
    
    for t in token_set:
        _ = get_decimals(t)  # Populate cache

    # Fetch prices
    pool_prices = await fetch_all_pool_prices(POOLS)
    if not pool_prices:
        log_to_file("No pool prices fetched, cannot proceed")
        return []

    # Build triangles
    log_to_file("Building triangular arbitrage candidates...")
    candidates = []
    for combo in combinations(POOLS, 3):
        toks = set(); edges = set(); ok = True
        for p in combo:
            if p not in pool_tokens:
                ok = False; break
            t0, t1 = pool_tokens[p]
            toks.update((t0, t1))
            edges.add(frozenset((t0, t1)))
        if ok and len(toks) == 3 and len(edges) == 3:
            candidates.append(combo)

    log_to_file(f"Found {len(candidates)} triangular arbitrage candidates")

    opportunities = []

    async def process_route(route):
        tokens = list({*pool_tokens[route[0]], *pool_tokens[route[1]], *pool_tokens[route[2]]})
        
        for perm in permutations(tokens, 3):
            legs = []
            leg_details = []
            product = Decimal(1)
            effective_rate = Decimal(1)
            total_fee_pct = Decimal(0)
            
            for i in range(3):
                t_in, t_out = perm[i], perm[(i + 1) % 3]
                
                # Find matching pool
                leg_pool = None
                for p in route:
                    if frozenset(pool_tokens[p]) == frozenset((t_in, t_out)):
                        leg_pool = p
                        break
                
                if not leg_pool:
                    break
                
                # Determine if we need to invert the price
                pool_t0, pool_t1 = pool_tokens[leg_pool]
                invert = t_in == pool_t1
                
                # Get token decimals
                d0 = get_decimals(pool_t0)
                d1 = get_decimals(pool_t1)
                
                # Get raw price data
                raw_bytes = pool_prices.get(leg_pool)
                if raw_bytes is None:
                    break
                
                try:
                    # Decode the price for this leg
                    raw_rate = leg_pool.decode_price(raw_bytes, invert, d0, d1)
                except Exception:
                    break
                
                # Calculate fee factor
                fee_factor = leg_pool._fee_factor()
                leg_fee_pct = (1 - fee_factor) * 100
                
                # Store leg details for output
                leg_details.append({
                    'pool': leg_pool,
                    'token_in': get_token_symbol(t_in),
                    'token_out': get_token_symbol(t_out),
                    'price': float(raw_rate),
                    'fee_pct': float(leg_fee_pct)
                })
                
                # Update running calculations
                product *= raw_rate
                effective_rate *= raw_rate * fee_factor
                total_fee_pct += leg_fee_pct
                legs.append(leg_pool)
            
            else:  # All legs completed successfully
                # Calculate gross delta (price difference)
                gross_delta = (product - 1) * 100
                
                # Calculate net profit after all fees
                net_profit = gross_delta - (total_fee_pct + AAVE_FEE_PCT)
                
                # Only show opportunities with positive net profit
                if net_profit > 0:
                    opportunities.append({
                        'route': legs,
                        'order': perm,
                        'leg_details': leg_details,
                        'gross_delta': float(gross_delta),
                        'profit_delta': float(net_profit),
                        'lp_fee_pct': float(total_fee_pct)
                    })

    # Process all candidates
    await asyncio.gather(*[process_route(c) for c in candidates])

    # Sort by profit
    opportunities.sort(key=lambda x: x['profit_delta'], reverse=True)
    
    return opportunities

async def main():
    """Main entry point."""
    # Clear log file at start
    clear_log_file()
    
    header = "Triangle Arbitrage Scanner"
    log_to_file("=" * 80)
    log_to_file(header)
    log_to_file("=" * 80)
    
    try:
        opportunities = await scan_routes()
        
        if opportunities:
            result_msg = f"\nFound {len(opportunities)} profitable opportunities:"
            log_to_file(result_msg)
            log_to_file("=" * 120)
            
            for i, opp in enumerate(opportunities[:10], 1):  # Show top 10
                log_to_file(f"\n{i:2d}. ARBITRAGE OPPORTUNITY")
                log_to_file("-" * 60)
                log_to_file(f"Net Profit: {opp['profit_delta']:+6.3f}% | Gross Delta: {opp['gross_delta']:+6.3f}% | Total DEX Fees: {opp['lp_fee_pct']:.3f}%")
                log_to_file("")
                
                # Show detailed leg information
                log_to_file("Route Details:")
                for j, leg in enumerate(opp['leg_details'], 1):
                    pool_label = leg['pool'].label or abbrev_addr(leg['pool'].addr)
                    log_to_file(f"  Leg {j}: {leg['token_in']} → {leg['token_out']}")
                    log_to_file(f"         Pool: {pool_label}")
                    log_to_file(f"         Price: {leg['price']:.8f} {leg['token_out']} per {leg['token_in']}")
                    log_to_file(f"         DEX Fee: {leg['fee_pct']:.3f}%")
                    log_to_file("")
                
                # Show token symbols in the route
                token_symbols = [get_token_symbol(t) for t in opp['order']]
                route_str = " → ".join(token_symbols + [token_symbols[0]])  # Complete the circle
                log_to_file(f"Token Route: {route_str}")
                log_to_file("")
        else:
            log_to_file("No profitable arbitrage opportunities found")
            
    except Exception as ex:
        error_msg = f"Fatal error in scanner: {ex}"
        log_to_file(error_msg)
    finally:
        log_to_file("Scanner finished.")

if __name__ == "__main__":
    asyncio.run(main())