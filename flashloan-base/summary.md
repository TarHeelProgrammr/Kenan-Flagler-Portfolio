# Manual Flash Loan on Base

## 📍 Overview
This prototype executes a flash loan using Aave V3 on the Base network, followed by a swap route.

## 🔧 Stack
- Solidity + Hardhat
- Python (for off-chain analytics)
- Node.js

| File | Description |
|------|-------------|
| [BlockScannerBot.cjs](./BlockScannerBot.cjs) | Scans for oppurtunties between two base WETH pools |
| [Documentation](./Documentation) | poorly made documentation I created, when I was still new to software development workflows |
| [FlashLoanArbitrageBot23.sol](./FlashLoanArbitrageBot23.sol) | solidity file for the smart contract |
| [IMG_6076.jpeg](./IMG_6076.jpeg) | _Add description here_ |
| [IMG_6079.png](./IMG_6079.png) | _Add description here_ |
| [Screenshot 2025-08-20 001842.png](./Screenshot%202025-08-20%20001842.png) | Terminal output of the .cjs scanner |
| [arbitrage_log.txt](./arbitrage_log.txt) | Data log of the .cjs scanner |
| [deploy.js](./deploy.js) | Script used to deploy smart contract to block chain |
| [interact.js](./interact.js) | Script used to call the contract |
