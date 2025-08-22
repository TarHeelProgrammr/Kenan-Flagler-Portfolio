# Triangular Arbitrage Prototype on Base

## 📍 Overview

The project includes three different scanners, each designed to detect three-legged triangular arbitrage oppurtunities. 
This project also includes a triangular arbitrage flashloan smart contract which did succesfully deploy, but did not work properly because the python scanner
wasn't working properly. 

I also beleive I deleted the deployment script, but we do have a deployed smart contract address in the documentation

## 🔧 Stack

- Solidity + Hardhat  
- Python (for off-chain analytics)  
- Node.js  

| File                             | Description                                                                       
|----------------------------------|-----------------------------------------------------------------------------------|
| [E_Scanner.py](./E_Scanner.py)             | Uses the same three pools every time and writes a working log                      
| [Scanner.py](./Scanner.py)                 | Dynamically generates triangular route candidates from pools.json; Fasley filtered profits due to no V3 math
| [liqap.py](./liqap.py)                     | Dynamically generates routes and integrates V3 math; Currently throws “tick out of bounds” errors                  
| [pools.json](./pools.json)                 | JSON list of all pools used for dynamic pool sourcing                              
| [scanner_log.txt](./scanner_log.txt)       | Data log output of EScanner.py                                       
| [TriFlashloan.sol](./contracts/TriFlashloan.sol) | Solidity contract for the triangular arbitrage flashloan                           
| [hardhat.config.js](./hardhat.config.js)   | Hardhat configuration for this project                                            
| [Picture of Escanner output]()   |                       
| [Picture of liqap output]() |   
| [Picture of Scanner outpit]() | 
| [requirements.txt]() |   lists all Python packages and their specific versions 
| [documentation.txt]() | Commands to activate enviornments and run scripts, and includes some personal notes to self 
| [package.json]() | Project configuration and dependencies 




