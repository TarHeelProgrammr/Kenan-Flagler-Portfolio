// SPDX-License-Identifier: MIT
pragma solidity ^0.7.6;
pragma abicoder v2;
import "@uniswap/v3-periphery/contracts/libraries/TransferHelper.sol";
import "@uniswap/v3-periphery/contracts/interfaces/ISwapRouter.sol";
import "@uniswap/v3-core/contracts/interfaces/callback/IUniswapV3FlashCallback.sol";
import "@uniswap/v3-core/contracts/interfaces/IUniswapV3Pool.sol";
import "@uniswap/v3-periphery/contracts/libraries/CallbackValidation.sol";
import "@uniswap/v3-periphery/contracts/libraries/PoolAddress.sol";
import "@openzeppelin/contracts/token/ERC20/IERC20.sol";


/// @title TriFlash
/// @notice Executes a 3‑leg Uniswap V3 flash‑swap triangle on Base:
///         WETH → cbBTC → USDC → WETH (all **0.05%** fee‑tier pools)
contract TriFlash is IUniswapV3FlashCallback {
    /* ─────── immutable config ─────── */
    address public immutable owner = msg.sender;
    address public immutable factory;
    ISwapRouter public immutable router;

    // Tokens (Base main‑net)
    address constant USDC = 0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913;
    address constant WETH = 0x4200000000000000000000000000000000000006;
    address constant CBBTC = 0xcbB7C0000aB88B473b1f5aFd9ef808440eed33Bf;

    // Uniswap V3 pools – all 0.05% (fee = 500)
    address constant P_USDC_WETH = 0xd0b53D9277642d899DF5C87A3966A349A798F224; // WETH/USDC pool
    address constant P_WETH_CBBTC = 0x8c7080564B5A792A33Ef2FD473fbA6364d5495e5; // WETH/cbBTC pool
    address constant P_CBBTC_USDC = 0xfBB6Eed8e7aa03B138556eeDaF5D271A5E1e43ef; // cbBTC/USDC pool

    constructor(address _factory, ISwapRouter _router) {
        factory = _factory;
        router = _router;
    }

    /* ─────── external entry ───────
       Borrow WETH from the USDC/WETH pool via flash‑swap.
       Since in the USDC/WETH pool token0 is WETH,
       we request the desired WETH amount as parameter amount0.
    */
    function initFlash(uint256 amountWeth) external {
        IUniswapV3Pool(P_USDC_WETH).flash(
            address(this),
            amountWeth, // amount0 = WETH (token0)
            0,
            abi.encode(amountWeth, msg.sender)
        );
    }

    /* ─────── Uniswap V3 flash callback ─────── */
    function uniswapV3FlashCallback(
        uint256 fee0,
        uint256 /* fee1 */,
        bytes calldata data
    ) external override {
        (uint256 amtWeth, address payer) = abi.decode(data, (uint256, address));
        address token0 = WETH;
        address token1 = USDC;
        uint24 fee = 500;
        // Create PoolKey for callback validation
        PoolAddress.PoolKey memory poolKey = PoolAddress.getPoolKey(WETH, USDC, 500);
        CallbackValidation.verifyCallback(factory, poolKey);

        /* 1) WETH → cbBTC (0.05%) */
        uint256 cbbtcOut = router.exactInputSingle(
            ISwapRouter.ExactInputSingleParams({
                tokenIn: WETH,
                tokenOut: CBBTC,
                fee: 500,
                recipient: address(this),
                deadline: block.timestamp,
                amountIn: amtWeth,
                amountOutMinimum: 0,
                sqrtPriceLimitX96: 0
            })
        );

        /* 2) cbBTC → USDC (0.05%) */
        uint256 usdcOut = router.exactInputSingle(
            ISwapRouter.ExactInputSingleParams({
                tokenIn: CBBTC,
                tokenOut: USDC,
                fee: 500,
                recipient: address(this),
                deadline: block.timestamp,
                amountIn: cbbtcOut,
                amountOutMinimum: 0,
                sqrtPriceLimitX96: 0
            })
        );

        /* 3) USDC → WETH (0.05%) */
        uint256 wethFinal = router.exactInputSingle(
            ISwapRouter.ExactInputSingleParams({
                tokenIn: USDC,
                tokenOut: WETH,
                fee: 500,
                recipient: address(this),
                deadline: block.timestamp,
                amountIn: usdcOut,
                amountOutMinimum: 0,
                sqrtPriceLimitX96: 0
            })
        );

        uint256 amountOwed = amtWeth + fee0; // principal (borrowed WETH) + flash fee
        require(wethFinal > amountOwed, "no profit");

        // Repay the flash‑swap with WETH
        TransferHelper.safeTransfer(WETH, P_USDC_WETH, amountOwed);

        // Send the profit (remaining WETH) to the caller who triggered initFlash()
        TransferHelper.safeTransfer(WETH, payer, wethFinal - amountOwed);
    }

    /* ─────── owner sweep (optional) ─────── */
    function withdraw(address token) external {
        require(msg.sender == owner, "only owner");
        uint256 bal = IERC20(token).balanceOf(address(this));
        require(bal > 0, "nothing to withdraw");
        TransferHelper.safeTransfer(token, owner, bal);
    }
}