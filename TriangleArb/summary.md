# Triangular Arbitrage Prototype on Base

## 📍 Overview

The project includes three different scanners, each designed to detect three-legged triangular arbitrage oppurtunities. One of the scanners, Escanner.py, scans the same triangular route each time. The other two scanners dynamically generate multiple routes by building them from a reference list of liqudity pool information in the pools.json file. Out of those two scanners only one of them incorporate V3 math. 

V2 liqudity pools are pools with liquidity spread uniformly acrros the entire price curve. While in V3 pools liquidity providers can deposit liquidity at certain ranges leading to fragmented liquidity across the price curve. It is important to incorporate the appropriate logic based off of pool version type (V2 or V3) to ensure accurate price data. 

This project also includes a triangular arbitrage flashloan smart contract which did succesfully deploy. However, the smart contract was never interacted with because the python scanner wasn't working properly. I also believe I deleted the deployment script, but we do have a deployed smart contract address in the documentation.

## 🔧 Stack

- Solidity + Hardhat  
- Python (for off-chain analytics)  
- Node.js  
- Base Mainnet RPC(HTTP) Alchemy 
- Base Mainnet RPC(Websockets) Alchemy


| File                                             | Description                                                                            |
|--------------------------------------------------|----------------------------------------------------------------------------------------|
| [EScanner.py](./Escanner.py)                     | Uses the same triangular route and records a log of price discrepancies (All pools are Uniswap V3 0.05% fee tier pools: USDC/WETH, cbBTC/WETH, cbBTC/USDC) |
| [Scanner.py](./scanner.py)                       | Dynamically generates triangular route candidates from pools.json; falsely filters profits due to no V3 math |
| [liqap.py](./liqap.py)                           | Dynamically generates routes and integrates V3 math; currently throws “tick out of bounds” errors |
| [pools.json](./pools.json)                       | JSON list of all pools used for dynamic pool sourcing                                 |
| [scanner_log.txt](./scanner_log.txt)             | Data log output of EScanner.py                                                         |
| [TriFlashloan.sol](./TriFlashloan.sol)           | Solidity contract for the triangular arbitrage flashloan                               |
| [hardhat.config.js](./hardhat.config.js)         | Hardhat configuration for this project                                                |
| [EscannerTerminalOut.png](./EscannerTerminalOut.png) | Terminal output of EScanner.py                                                    |
| [liqapOutput.png](./liqapOutput.png)             | Terminal output of liqap.py’s tick out of bounds error                                 |
| [ScannerTerminalOut.png](./ScannerTerminalOut.png) | Terminal output of scanner.py                                                       |
| [requirements.txt](./requirements.txt)           | Lists all Python packages and their specific versions                                 |
| [Documentation.txt](./Documentation.txt)         | Commands to activate environments and run scripts; includes personal notes to self     |
| [package.json](./package.json)                   | Project configuration and dependencies                                                |



