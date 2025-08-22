from __future__ import annotations

import asyncio
import json
import logging
import os
from decimal import Decimal, getcontext
from itertools import combinations, permutations
from typing import Optional, Dict, List, Tuple
import math

from dotenv import load_dotenv
from web3 import Web3
from web3.exceptions import ContractLogicError

# ─── Global precision for Decimal maths ──────────────────────────────────────
getcontext().prec = 50

# ─── Paths & env ─────────────────────────────────────────────────────────────
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.join(CURRENT_DIR, "..")
ENV_PATH = os.path.join(PROJECT_ROOT, ".env")
POOLS_CFG_FP = os.path.join(CURRENT_DIR, "pools.json")
LOG_FILE_PATH = os.path.join(PROJECT_ROOT, "liqap_log.txt")

load_dotenv(ENV_PATH)

BASE_RPC = os.getenv("BASE_RPC")
BASE_WEBSOCKET = os.getenv("BASE_WEBSOCKET")
if not BASE_RPC or not BASE_WEBSOCKET:
    raise RuntimeError("BASE_RPC / BASE_WEBSOCKET missing in .env")

AAVE_FEE_PCT = Decimal("0.05")  # 0.05% flash-loan fee
MAX_PROFIT_THRESHOLD = Decimal("15.0")  # 15% max realistic profit per trade

# Multicall3 contract address on Base network
MULTICALL3_ADDRESS = "0xcA11bde05977b3631167028862bE2a173976CA11"

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
log = logging.getLogger("liqap")

# ─── Web3 ────────────────────────────────────────────────────────────────────
# Initialize Web3 connection once at module level
w3 = Web3(Web3.HTTPProvider(BASE_RPC))
assert w3.is_connected(), "RPC provider is unreachable"

# Cache chain ID to avoid repeated calls
CHAIN_ID = w3.eth.chain_id
log.info(f"Connected to chain ID: {CHAIN_ID}")

# Method selectors
SEL_SLOT0 = "0x3850c7bd"  # Uniswap V3 slot0()
SEL_RESERVES = "0x0902f1ac"  # Uniswap V2 getReserves()
SEL_LIQUIDITY = "0x1a686502"  # Uniswap V3 liquidity()
SEL_TICK_SPACING = "0xd0c93a7c"  # Uniswap V3 tickSpacing()

# Multicall3 ABI (minimal - only aggregate3 function)
MULTICALL3_ABI = [
    {
        "inputs": [
            {
                "components": [
                    {"internalType": "address", "name": "target", "type": "address"},
                    {"internalType": "bool", "name": "allowFailure", "type": "bool"},
                    {"internalType": "bytes", "name": "callData", "type": "bytes"}
                ],
                "internalType": "struct Multicall3.Call3[]",
                "name": "calls",
                "type": "tuple[]"
            }
        ],
        "name": "aggregate3",
        "outputs": [
            {
                "components": [
                    {"internalType": "bool", "name": "success", "type": "bool"},
                    {"internalType": "bytes", "name": "returnData", "type": "bytes"}
                ],
                "internalType": "struct Multicall3.Result[]",
                "name": "returnData",
                "type": "tuple[]"
            }
        ],
        "stateMutability": "payable",
        "type": "function"
    }
]

# Initialize Multicall3 contract
multicall3_contract = w3.eth.contract(
    address=Web3.to_checksum_address(MULTICALL3_ADDRESS),
    abi=MULTICALL3_ABI
)

# ─── V3 Math Constants ──────────────────────────────────────────────────────
Q96 = 2 ** 96
MIN_TICK = -887272
MAX_TICK = 887272
MIN_SQRT_RATIO = 4295128739
MAX_SQRT_RATIO = 1461446703485210103287273052203988822378723970342

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
        f.write("Concentrated Liquidity Scanner Log\n")
        f.write("=" * 50 + "\n\n")

# ─── V3 Math Functions ──────────────────────────────────────────────────────
def tick_to_sqrt_price_x96(tick: int) -> int:
    """Convert tick to sqrtPriceX96."""
    if tick < MIN_TICK or tick > MAX_TICK:
        raise ValueError(f"Tick {tick} out of bounds")
    
    abs_tick = abs(tick)
    ratio = 0xfffcb933bd6fad37aa2d162d1a594001 if abs_tick & 0x1 != 0 else 0x100000000000000000000000000000000
    
    if abs_tick & 0x2 != 0:
        ratio = (ratio * 0xfff97272373d413259a46990580e213a) >> 128
    if abs_tick & 0x4 != 0:
        ratio = (ratio * 0xfff2e50f5f656932ef12357cf3c7fdcc) >> 128
    if abs_tick & 0x8 != 0:
        ratio = (ratio * 0xffe5caca7e10e4e61c3624eaa0941cd0) >> 128
    if abs_tick & 0x10 != 0:
        ratio = (ratio * 0xffcb9843d60f6159c9db58835c926644) >> 128
    if abs_tick & 0x20 != 0:
        ratio = (ratio * 0xff973b41fa98c081472e6896dfb254c0) >> 128
    if abs_tick & 0x40 != 0:
        ratio = (ratio * 0xff2ea16466c96a3843ec78b326b52861) >> 128
    if abs_tick & 0x80 != 0:
        ratio = (ratio * 0xfe5dee046a99a2a811c461f1969c3053) >> 128
    if abs_tick & 0x100 != 0:
        ratio = (ratio * 0xfcbe86c7900a88aedcffc83b479aa3a4) >> 128
    if abs_tick & 0x200 != 0:
        ratio = (ratio * 0xf987a7253ac413176f2b074cf7815e54) >> 128
    if abs_tick & 0x400 != 0:
        ratio = (ratio * 0xf3392b0822b70005940c7a398e4b70f3) >> 128
    if abs_tick & 0x800 != 0:
        ratio = (ratio * 0xe7159475a2c29b7443b29c7fa6e889d9) >> 128
    if abs_tick & 0x1000 != 0:
        ratio = (ratio * 0xd097f3bdfd2022b8845ad8f792aa5825) >> 128
    if abs_tick & 0x2000 != 0:
        ratio = (ratio * 0xa9f746462d870fdf8a65dc1f90e061e5) >> 128
    if abs_tick & 0x4000 != 0:
        ratio = (ratio * 0x70d869a156d2a1b890bb3df62baf32f7) >> 128
    if abs_tick & 0x8000 != 0:
        ratio = (ratio * 0x31be135f97d08fd981231505542fcfa6) >> 128
    if abs_tick & 0x10000 != 0:
        ratio = (ratio * 0x9aa508b5b7a84e1c677de54f3e99bc9) >> 128
    if abs_tick & 0x20000 != 0:
        ratio = (ratio * 0x5d6af8dedb81196699c329225ee604) >> 128
    if abs_tick & 0x40000 != 0:
        ratio = (ratio * 0x2216e584f5fa1ea926041bedfe98) >> 128
    if abs_tick & 0x80000 != 0:
        ratio = (ratio * 0x48a170391f7dc42444e8fa2) >> 128

    if tick > 0:
        ratio = (2**256 - 1) // ratio

    return (ratio >> 32) + (1 if ratio % (1 << 32) > 0 else 0)

def sqrt_price_x96_to_tick(sqrt_price_x96: int) -> int:
    """Convert sqrtPriceX96 to tick (approximate)."""
    if sqrt_price_x96 < MIN_SQRT_RATIO or sqrt_price_x96 > MAX_SQRT_RATIO:
        raise ValueError(f"sqrtPriceX96 {sqrt_price_x96} out of bounds")
    
    # Use binary search to find the tick
    low, high = MIN_TICK, MAX_TICK
    
    while low <= high:
        mid = (low + high) // 2
        sqrt_price_mid = tick_to_sqrt_price_x96(mid)
        
        if sqrt_price_mid == sqrt_price_x96:
            return mid
        elif sqrt_price_mid < sqrt_price_x96:
            low = mid + 1
        else:
            high = mid - 1
    
    return high

def get_amount_0_delta(sqrt_ratio_a_x96: int, sqrt_ratio_b_x96: int, liquidity: int) -> int:
    """Calculate amount0 delta for a liquidity range."""
    if sqrt_ratio_a_x96 > sqrt_ratio_b_x96:
        sqrt_ratio_a_x96, sqrt_ratio_b_x96 = sqrt_ratio_b_x96, sqrt_ratio_a_x96
    
    return int((liquidity * Q96 * (sqrt_ratio_b_x96 - sqrt_ratio_a_x96)) // (sqrt_ratio_a_x96 * sqrt_ratio_b_x96))

def get_amount_1_delta(sqrt_ratio_a_x96: int, sqrt_ratio_b_x96: int, liquidity: int) -> int:
    """Calculate amount1 delta for a liquidity range."""
    if sqrt_ratio_a_x96 > sqrt_ratio_b_x96:
        sqrt_ratio_a_x96, sqrt_ratio_b_x96 = sqrt_ratio_b_x96, sqrt_ratio_a_x96
    
    return int((liquidity * (sqrt_ratio_b_x96 - sqrt_ratio_a_x96)) // Q96)

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

    def simulate_swap(self, amount_in: int, zero_for_one: bool, sqrt_price_limit_x96: int = 0) -> tuple[int, int]:
        """Simulate a swap and return (amount_out, new_sqrt_price_x96)."""
        raise NotImplementedError

class V3Pool(BasePool):
    def __init__(self, addr: str, fee_ppm: Optional[int], hint: list[str] | None = None):
        super().__init__(addr, fee_ppm, hint)
        self._slot0_data: dict | None = None
        self._liquidity: int | None = None
        self._tick_spacing: int | None = None

    def build_call(self):
        return {"to": self.addr, "data": SEL_SLOT0}

    def build_extended_calls(self) -> List[dict]:
        """Build calls for slot0, liquidity, and tickSpacing."""
        return [
            {"to": self.addr, "data": SEL_SLOT0},
            {"to": self.addr, "data": SEL_LIQUIDITY},
            {"to": self.addr, "data": SEL_TICK_SPACING}
        ]

    def decode_extended_data(self, slot0_data: bytes, liquidity_data: bytes, tick_spacing_data: bytes):
        """Decode extended pool data."""
        try:
            # Decode slot0
            slot0_hex = _norm(slot0_data)
            sqrt_price_x96 = int(slot0_hex[2:66], 16)
            tick = int.from_bytes(bytes.fromhex(slot0_hex[66:130]), byteorder='big', signed=True)
            
            # Decode liquidity
            liquidity_hex = _norm(liquidity_data)
            liquidity = int(liquidity_hex[2:66], 16)
            
            # Decode tick spacing
            tick_spacing_hex = _norm(tick_spacing_data)
            tick_spacing = int(tick_spacing_hex[2:66], 16)
            
            self._slot0_data = {
                'sqrt_price_x96': sqrt_price_x96,
                'tick': tick
            }
            self._liquidity = liquidity
            self._tick_spacing = tick_spacing
            
        except Exception as ex:
            raise ValueError(f"Failed to decode V3 pool data: {ex}")

    def simulate_swap(self, amount_in: int, zero_for_one: bool, sqrt_price_limit_x96: int = 0) -> tuple[int, int]:
        """Simulate a V3 swap with concentrated liquidity math."""
        if not self._slot0_data or self._liquidity is None:
            raise ValueError("Pool data not loaded")
        
        current_sqrt_price = self._slot0_data['sqrt_price_x96']
        current_tick = self._slot0_data['tick']
        liquidity = self._liquidity
        
        # Set price limit if not provided
        if sqrt_price_limit_x96 == 0:
            sqrt_price_limit_x96 = MIN_SQRT_RATIO + 1 if zero_for_one else MAX_SQRT_RATIO - 1
        
        # Simplified simulation - assumes all liquidity is concentrated at current tick
        # In reality, we'd need to iterate through ticks and consume liquidity
        
        amount_remaining = amount_in
        amount_out = 0
        
        # Apply fee
        fee_factor = self._fee_factor()
        amount_in_after_fee = int(amount_in * fee_factor)
        
        if zero_for_one:
            # Swapping token0 for token1
            # Calculate how much token1 we get for the token0 input
            amount_out = get_amount_1_delta(
                current_sqrt_price,
                sqrt_price_limit_x96,
                liquidity
            )
            
            # Limit output to what's actually available
            max_amount_1 = get_amount_1_delta(
                current_sqrt_price,
                tick_to_sqrt_price_x96(current_tick - self._tick_spacing),
                liquidity
            )
            amount_out = min(amount_out, max_amount_1)
            
        else:
            # Swapping token1 for token0
            amount_out = get_amount_0_delta(
                sqrt_price_limit_x96,
                current_sqrt_price,
                liquidity
            )
            
            # Limit output to what's actually available
            max_amount_0 = get_amount_0_delta(
                tick_to_sqrt_price_x96(current_tick + self._tick_spacing),
                current_sqrt_price,
                liquidity
            )
            amount_out = min(amount_out, max_amount_0)
        
        # Calculate new sqrt price (simplified)
        if zero_for_one:
            new_sqrt_price = current_sqrt_price - int((amount_in_after_fee * Q96) // liquidity)
        else:
            new_sqrt_price = current_sqrt_price + int((amount_in_after_fee * Q96) // liquidity)
        
        # Ensure price stays within bounds
        new_sqrt_price = max(MIN_SQRT_RATIO, min(MAX_SQRT_RATIO, new_sqrt_price))
        
        return abs(amount_out), new_sqrt_price

    def decode_price(self, data, invert, d0, d1):
        """Legacy method for compatibility - uses spot price."""
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
        self._reserves: tuple[int, int] | None = None

    def build_call(self):
        return {"to": self.addr, "data": SEL_RESERVES}

    def build_extended_calls(self) -> List[dict]:
        """V2 only needs reserves."""
        return [self.build_call()]

    def decode_extended_data(self, reserves_data: bytes):
        """Decode V2 reserves data."""
        try:
            data = _norm(reserves_data)
            r0 = int(data[2:66], 16)
            r1 = int(data[66:130], 16)
            validate_reserves(r0, r1, self.addr)
            self._reserves = (r0, r1)
        except Exception as ex:
            raise ValueError(f"Failed to decode V2 reserves: {ex}")

    def simulate_swap(self, amount_in: int, zero_for_one: bool, sqrt_price_limit_x96: int = 0) -> tuple[int, int]:
        """Simulate a V2 swap using constant product formula."""
        if not self._reserves:
            raise ValueError("Reserves not loaded")
        
        reserve_in, reserve_out = self._reserves if zero_for_one else (self._reserves[1], self._reserves[0])
        
        # Apply fee
        fee_factor = self._fee_factor()
        amount_in_with_fee = int(amount_in * fee_factor)
        
        # Constant product formula: (x + dx) * (y - dy) = x * y
        # Solving for dy: dy = (y * dx) / (x + dx)
        amount_out = (reserve_out * amount_in_with_fee) // (reserve_in + amount_in_with_fee)
        
        # Calculate new "price" (not really applicable to V2, but for consistency)
        new_reserve_in = reserve_in + amount_in
        new_reserve_out = reserve_out - amount_out
        new_price_ratio = (new_reserve_out * Q96) // new_reserve_in  # Simplified representation
        
        return amount_out, new_price_ratio

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

class AerodromeStaticPool(V2Pair):
    """Aerodrome static pools behave like V2 pairs."""
    pass

class AerodromeMetapool(V3Pool):
    """Aerodrome metapools are concentrated liquidity pools like V3."""
    
    def __init__(self, addr: str, fee_ppm: Optional[int], hint: list[str] | None = None):
        # Aerodrome metapools have dynamic fees, so we don't set a fixed fee
        super().__init__(addr, fee_ppm, hint)
        self.dynamic_fee = True  # Always dynamic for metapools
    
    def _fee_factor(self) -> Decimal:
        """Get fee factor for Aerodrome metapools with dynamic fees."""
        if self.dynamic_fee:
            try:
                # Try to get dynamic fee from the pool
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
                fee_factor = Decimal(1) - Decimal(fee_ppm) / Decimal(1_000_000)
                return fee_factor
            except Exception:
                # Fallback to a reasonable default for Aerodrome (typically around 0.05-0.3%)
                default_fee_ppm = 500  # 0.05%
                fee_factor = Decimal(1) - Decimal(default_fee_ppm) / Decimal(1_000_000)
                return fee_factor
        else:
            # Should not happen for metapools, but fallback
            fee_ppm = self.fee_ppm or 500
            fee_factor = Decimal(1) - Decimal(fee_ppm) / Decimal(1_000_000)
            return fee_factor

# Registry
PROTO = {
    "uniswap_v3": V3Pool,
    "sushiswap_v3": V3Pool,
    "pancakeswap_v3": V3Pool,
    "uniswap_v2": V2Pair,
    "aerodrome_static": AerodromeStaticPool,
    "aerodrome_metapool": AerodromeMetapool,
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

async def fetch_extended_pool_data_multicall(pools: list[BasePool]):
    """Fetch extended pool data using Multicall3 for concentrated liquidity calculations."""
    
    log_to_file(f"Preparing extended multicall for {len(pools)} pools...")
    
    # Prepare multicall data
    calls = []
    pool_call_mapping = []  # Track which pool and call type corresponds to each call
    
    for pool in pools:
        try:
            extended_calls = pool.build_extended_calls()
            for call_data in extended_calls:
                # Create Call3 struct: (target, allowFailure, callData)
                multicall_entry = (
                    Web3.to_checksum_address(call_data["to"]),  # target
                    True,  # allowFailure - don't let one failure break the batch
                    bytes.fromhex(call_data["data"][2:])  # callData (remove 0x prefix)
                )
                calls.append(multicall_entry)
                pool_call_mapping.append(pool)
        except Exception as e:
            log_to_file(f"Failed to prepare extended calls for pool {abbrev_addr(pool.addr)}: {e}")
            continue
    
    if not calls:
        log_to_file("No valid extended calls to make")
        return {}
    
    log_to_file(f"Making extended multicall with {len(calls)} batched requests...")
    
    try:
        # Make the single multicall request
        results = await asyncio.to_thread(
            multicall3_contract.functions.aggregate3(calls).call
        )
        
        log_to_file(f"Extended multicall completed successfully, processing {len(results)} results...")
        
        # Process results and group by pool
        successful_pools = {}
        failed_count = 0
        
        i = 0
        for pool in pools:
            try:
                if isinstance(pool, (V3Pool, AerodromeMetapool)):
                    # V3 pools and Aerodrome metapools need 3 calls: slot0, liquidity, tickSpacing
                    if i + 2 < len(results):
                        slot0_success, slot0_data = results[i]
                        liquidity_success, liquidity_data = results[i + 1]
                        tick_spacing_success, tick_spacing_data = results[i + 2]
                        
                        if slot0_success and liquidity_success and tick_spacing_success:
                            pool.decode_extended_data(slot0_data, liquidity_data, tick_spacing_data)
                            successful_pools[pool] = slot0_data  # Keep for compatibility
                        else:
                            failed_count += 1
                            log_to_file(f"Failed extended calls for {pool.label or abbrev_addr(pool.addr)}")
                        
                        i += 3
                    else:
                        failed_count += 1
                        break
                        
                elif isinstance(pool, (V2Pair, AerodromeStaticPool)):
                    # V2 pools need 1 call: reserves
                    if i < len(results):
                        reserves_success, reserves_data = results[i]
                        
                        if reserves_success and reserves_data:
                            pool.decode_extended_data(reserves_data)
                            successful_pools[pool] = reserves_data  # Keep for compatibility
                        else:
                            failed_count += 1
                            log_to_file(f"Failed extended calls for {pool.label or abbrev_addr(pool.addr)}")
                        
                        i += 1
                    else:
                        failed_count += 1
                        break
                        
            except Exception as e:
                log_to_file(f"Failed to process extended data for pool {abbrev_addr(pool.addr)}: {e}")
                failed_count += 1
                continue
        
        # Log summary
        success_count = len(successful_pools)
        log_to_file(f"Extended multicall results: {success_count} successful, {failed_count} failed")
        log_to_file(f"RPC efficiency: Reduced {len(calls)} individual calls to 1 multicall")
        
        return successful_pools
        
    except Exception as e:
        log_to_file(f"Extended multicall failed: {e}")
        return {}

def get_token_amount_for_usd(token_address: str, usd_amount: float) -> int:
    """Calculate token amount (in wei) for a given USD value."""
    checksum_addr = Web3.to_checksum_address(token_address)
    if checksum_addr not in TOKEN_PRICES:
        log_to_file(f"No price data for token {token_address}")
        return int(10000 * 10**18)  # Fallback to 10000 tokens with 18 decimals
    
    token_price = TOKEN_PRICES[checksum_addr]
    token_amount = Decimal(usd_amount) / token_price
    
    # Get token decimals and convert to wei
    decimals = get_decimals(token_address)
    token_amount_wei = int(token_amount * (10 ** decimals))
    
    return token_amount_wei

async def scan_routes():
    """Scan all arbitrage routes using concentrated liquidity math."""
    log_to_file("Starting concentrated liquidity arbitrage route scanning...")
    
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

    # Fetch extended pool data using multicall
    pool_data = await fetch_extended_pool_data_multicall(POOLS)
    if not pool_data:
        log_to_file("No pool data fetched, cannot proceed")
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
            
            # Start with $10,000 worth of the first token
            initial_usd_amount = 10000
            current_amount = get_token_amount_for_usd(perm[0], initial_usd_amount)
            
            total_fee_pct = Decimal(0)
            
            for i in range(3):
                t_in, t_out = perm[i], perm[(i + 1) % 3]
                
                # Find matching pool
                leg_pool = None
                for p in route:
                    if p not in pool_data:
                        continue
                    if frozenset(pool_tokens[p]) == frozenset((t_in, t_out)):
                        leg_pool = p
                        break
                
                if not leg_pool:
                    break
                
                # Determine swap direction
                pool_t0, pool_t1 = pool_tokens[leg_pool]
                zero_for_one = t_in == pool_t0
                
                try:
                    # Simulate the actual swap with slippage
                    amount_out, new_price = leg_pool.simulate_swap(
                        current_amount, 
                        zero_for_one
                    )
                    
                    # Calculate effective rate for this leg
                    if current_amount > 0:
                        # Adjust for decimals
                        decimals_in = get_decimals(t_in)
                        decimals_out = get_decimals(t_out)
                        
                        amount_in_normalized = Decimal(current_amount) / Decimal(10 ** decimals_in)
                        amount_out_normalized = Decimal(amount_out) / Decimal(10 ** decimals_out)
                        
                        effective_rate = amount_out_normalized / amount_in_normalized if amount_in_normalized > 0 else Decimal(0)
                    else:
                        effective_rate = Decimal(0)
                    
                    # Calculate fee percentage for this leg
                    fee_factor = leg_pool._fee_factor()
                    leg_fee_pct = (1 - fee_factor) * 100
                    
                    # Store leg details for output
                    leg_details.append({
                        'pool': leg_pool,
                        'token_in': get_token_symbol(t_in),
                        'token_out': get_token_symbol(t_out),
                        'amount_in': current_amount,
                        'amount_out': amount_out,
                        'effective_rate': float(effective_rate),
                        'fee_pct': float(leg_fee_pct),
                        'slippage_included': True
                    })
                    
                    total_fee_pct += leg_fee_pct
                    legs.append(leg_pool)
                    current_amount = amount_out
                    
                except Exception as e:
                    log_to_file(f"Failed to simulate swap for {get_token_symbol(t_in)} -> {get_token_symbol(t_out)}: {e}")
                    break
            
            else:  # All legs completed successfully
                # Calculate final value in USD
                final_token = perm[0]  # We end up back at the starting token
                final_usd_value = 0
                
                if final_token in [Web3.to_checksum_address(addr) for addr in TOKEN_PRICES.keys()]:
                    checksum_final = Web3.to_checksum_address(final_token)
                    token_price = TOKEN_PRICES[checksum_final]
                    decimals_final = get_decimals(final_token)
                    final_amount_normalized = Decimal(current_amount) / Decimal(10 ** decimals_final)
                    final_usd_value = float(final_amount_normalized * token_price)
                
                # Calculate profit
                gross_profit_usd = final_usd_value - initial_usd_amount
                gross_profit_pct = (gross_profit_usd / initial_usd_amount) * 100
                
                # Calculate net profit after flash loan fee
                flash_loan_fee_usd = initial_usd_amount * float(AAVE_FEE_PCT) / 100
                net_profit_usd = gross_profit_usd - flash_loan_fee_usd
                net_profit_pct = (net_profit_usd / initial_usd_amount) * 100
                
                # Only show opportunities with positive net profit
                if net_profit_pct > 0:
                    opportunities.append({
                        'route': legs,
                        'order': perm,
                        'leg_details': leg_details,
                        'initial_usd': initial_usd_amount,
                        'final_usd': final_usd_value,
                        'gross_profit_usd': gross_profit_usd,
                        'gross_profit_pct': gross_profit_pct,
                        'net_profit_usd': net_profit_usd,
                        'net_profit_pct': net_profit_pct,
                        'total_dex_fee_pct': float(total_fee_pct),
                        'flash_loan_fee_pct': float(AAVE_FEE_PCT)
                    })

    # Process all candidates
    await asyncio.gather(*[process_route(c) for c in candidates])

    # Sort by net profit percentage
    opportunities.sort(key=lambda x: x['net_profit_pct'], reverse=True)
    
    return opportunities

async def main():
    """Main entry point."""
    # Clear log file at start
    clear_log_file()
    
    header = "Concentrated Liquidity Arbitrage Scanner"
    log_to_file("=" * 80)
    log_to_file(header)
    log_to_file("=" * 80)
    
    try:
        opportunities = await scan_routes()
        
        if opportunities:
            result_msg = f"\nFound {len(opportunities)} profitable opportunities with slippage:"
            log_to_file(result_msg)
            log_to_file("=" * 120)
            
            for i, opp in enumerate(opportunities[:10], 1):  # Show top 10
                log_to_file(f"\n{i:2d}. CONCENTRATED LIQUIDITY ARBITRAGE OPPORTUNITY")
                log_to_file("-" * 70)
                log_to_file(f"Net Profit: ${opp['net_profit_usd']:+,.2f} ({opp['net_profit_pct']:+6.3f}%)")
                log_to_file(f"Gross Profit: ${opp['gross_profit_usd']:+,.2f} ({opp['gross_profit_pct']:+6.3f}%)")
                log_to_file(f"Initial Amount: ${opp['initial_usd']:,.2f} | Final Value: ${opp['final_usd']:,.2f}")
                log_to_file(f"Total DEX Fees: {opp['total_dex_fee_pct']:.3f}% | Flash Loan Fee: {opp['flash_loan_fee_pct']:.3f}%")
                log_to_file("")
                
                # Show detailed leg information with slippage
                log_to_file("Route Details (with slippage simulation):")
                for j, leg in enumerate(opp['leg_details'], 1):
                    pool_label = leg['pool'].label or abbrev_addr(leg['pool'].addr)
                    log_to_file(f"  Leg {j}: {leg['token_in']} → {leg['token_out']}")
                    log_to_file(f"         Pool: {pool_label}")
                    log_to_file(f"         Amount In: {leg['amount_in']:,} wei")
                    log_to_file(f"         Amount Out: {leg['amount_out']:,} wei")
                    log_to_file(f"         Effective Rate: {leg['effective_rate']:.8f} {leg['token_out']} per {leg['token_in']}")
                    log_to_file(f"         DEX Fee: {leg['fee_pct']:.3f}%")
                    log_to_file(f"         Slippage: {'Included' if leg['slippage_included'] else 'Not calculated'}")
                    log_to_file("")
                
                # Show token symbols in the route
                token_symbols = [get_token_symbol(t) for t in opp['order']]
                route_str = " → ".join(token_symbols + [token_symbols[0]])  # Complete the circle
                log_to_file(f"Token Route: {route_str}")
                log_to_file("")
        else:
            log_to_file("No profitable arbitrage opportunities found with concentrated liquidity calculations")
            
    except Exception as ex:
        error_msg = f"Fatal error in concentrated liquidity scanner: {ex}"
        log_to_file(error_msg)
    finally:
        log_to_file("Concentrated liquidity scanner finished.")

# Function for running in a loop without re-initializing Web3
async def scan_loop(interval_seconds: int = 30):
    """Run the concentrated liquidity scanner in a loop with specified interval."""
    log_to_file(f"Starting concentrated liquidity scanner loop with {interval_seconds}s intervals...")
    
    iteration = 0
    while True:
        iteration += 1
        log_to_file(f"\n{'='*20} ITERATION {iteration} {'='*20}")
        
        try:
            opportunities = await scan_routes()
            
            if opportunities:
                log_to_file(f"Found {len(opportunities)} opportunities in iteration {iteration}")
                # Log top opportunity
                top_opp = opportunities[0]
                log_to_file(f"Best opportunity: ${top_opp['net_profit_usd']:+,.2f} ({top_opp['net_profit_pct']:+.3f}%) net profit")
            else:
                log_to_file(f"No opportunities found in iteration {iteration}")
                
        except Exception as e:
            log_to_file(f"Error in iteration {iteration}: {e}")
        
        log_to_file(f"Waiting {interval_seconds}s before next scan...")
        await asyncio.sleep(interval_seconds)

if __name__ == "__main__":
    # Run single scan
    asyncio.run(main())
    
    # Uncomment below to run in loop mode instead
    # asyncio.run(scan_loop(30))  # 30 second intervals