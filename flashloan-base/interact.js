const { ethers } = require("hardhat");

async function main() {
    // Replace with your deployed FlashLoanEth contract address
    const CONTRACT_ADDRESS = "0xa4d59876DcDAf542acA62A3d6395A6b82D749805"; // Replace with your contract address

    // Replace with the USDC token address on Sepolia network
    const ASSET_ADDRESS = "0x94a9D9AC8a22534E3FaCa9F4e7F2E2cf85d5E4C8"; // Replace with correct USDC address

    // Amount to borrow (10 USDC with 6 decimals)
    const AMOUNT = ethers.parseUnits("1000", 6);

    // Attach to the deployed contract
    const FlashLoan = await ethers.getContractFactory("FlashLoanEth");
    const flashLoan = FlashLoan.attach(CONTRACT_ADDRESS);

    // Check contract balance
    const usdc = await ethers.getContractAt("IERC20", ASSET_ADDRESS);
    const balance = await usdc.balanceOf(CONTRACT_ADDRESS);
    console.log(`Contract USDC Balance: ${ethers.formatUnits(balance, 6)} USDC`);

    // Request a flash loan
    console.log(`Requesting a flash loan of ${ethers.formatUnits(AMOUNT, 6)} USDC...`);
    const tx = await flashLoan.executeFlashLoan(ASSET_ADDRESS, AMOUNT);

    // Wait for the transaction to complete
    console.log("Transaction submitted. Waiting for confirmation...");
    const receipt = await tx.wait();
    console.log(`Flash loan executed successfully! Transaction hash: ${receipt.hash}`);
}

// Handle errors
main().catch((error) => {
    console.error("Error executing the flash loan:", error);
    process.exitCode = 1;
});
