# Manual Flash Loan on Base

## 📍 Overview

This prototype has a basic flashloan which borrows and returns 1000 usdc without any arbitrage logic. It was the first working flash loan
I ever executed, and it was the first big milestone in my development journey.  This folder also includes a scanner that attempts to find price 
discrepancies between two fixed tier base WETH/USDC liquidty pools on sushiswap and uniswap. This scanner tried to incorporate the use of a 
'borrowing advantage' variable. A profit margin which I thought existed in theory, but later realized the mathematical logic was incorrect. 

## 🔧 Stack
- Solidity + Hardhat
- Python (for off-chain analytics)
- Node.js

| File | Description |
|------|-------------|
| [BlockScannerBot.cjs](./BlockScannerBot.cjs) | Scans for oppurtunties between two base WETH pools |
| [Documentation](./Documentation) | poorly made documentation I created, when I was still new to software development workflows |
| [FlashLoanArbitrageBot23.sol](./FlashLoanArbitrageBot23.sol) | solidity file for the smart contract |
| [IMG_6076.jpeg](./IMG_6076.jpeg) | Picture of my first ever flashloan being executed in a terminal |
| [IMG_6079.png](./IMG_6079.png) | Zoomed in picture of my first ever flashloan being executed in a terminal |
| [Screenshot 2025-08-20 001842.png](./Screenshot%202025-08-20%20001842.png) | Terminal output of the .cjs scanner |
| [arbitrage_log.txt](./arbitrage_log.txt) | Data log of the .cjs scanner |
| [deploy.js](./deploy.js) | Script used to deploy smart contract to block chain |
| [interact.js](./interact.js) | Script used to call the contract |
