require('dotenv').config();
const { ethers } = require("hardhat");

async function main() {
    const providerAddress = "0x012bAC54348C0E635dCAc9D5FB99f06F24136C9A"; // Ensure this is the correct Aave PoolAddressesProvider on Sepolia
    const FlashLoan = await ethers.getContractFactory("FlashLoanEth");
    const flashLoan = await FlashLoan.deploy(providerAddress);
    await flashLoan.waitForDeployment();
    
    // In Ethers v6, use getAddress() to retrieve contract address
    console.log("FlashLoan deployed to:", await flashLoan.getAddress());
}

main()
    .then(() => process.exit(0))
    .catch((error) => {
        console.error(error);
        process.exit(1);
    });

