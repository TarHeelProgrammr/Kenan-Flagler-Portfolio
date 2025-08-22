# Triangular Arbitrage Prototype on Base

## 📍 Overview

The project includes three different scanners, each designed to detect three-legged triangular arbitrage oppurtunities. One of the scanners, Escanner.py, scans the same triangular route each time. The other two scanners dynamically generate multiple routes by building them from a reference list of liqudity pool information in the pools.json file. Out of those two scanners only one of them incorporate V3 math. 

V2 liqudity pools are pools with liquduity spread uniformly acrros the entire price curve. While in V3 pools liquidty providers can deposit liqudity at certain ranges leading to fragmented liqudity across the price curve. It is important to incorporate the appropriate logic based of version type to ensure accurate price data. 

This project also includes a triangular arbitrage flashloan smart contract which did succesfully deploy. However, the smart contract was never interacted with because the python scanner wasn't working properly. I also believe I deleted the deployment script, but we do have a deployed smart contract address in the documentation.

## 🔧 Stack

- Solidity + Hardhat  
- Python (for off-chain analytics)  
- Node.js  

| File                             | Description                                                                       
|----------------------------------|-----------------------------------------------------------------------------------|
| [EScanner.py](./Escanner.py)             | Uses the same three pools every time and writes a working log                      
| [Scanner.py](./scanner.py)                 | Dynamically generates triangular route candidates from pools.json; Fasley filtered profits due to no V3 math
| [liqap.py](./liqap.py)                     | Dynamically generates routes and integrates V3 math; Currently throws “tick out of bounds” errors                  
| [pools.json](./pools.json)                 | JSON list of all pools used for dynamic pool sourcing                              
| [scanner_log.txt](./scanner_log.txt)       | Data log output of EScanner.py                                       
| [TriFlashloan.sol](./TriFlashloan.sol) | Solidity contract for the triangular arbitrage flashloan                           
| [hardhat.config.js](./hardhat.config.js)   | Hardhat configuration for this project                                            
| [Picture of Escanner output](./EscannerTerminalOut.png)   | Terminal output of Escanner.py                  
| [Picture of liqap output](./liqapOutput.png) |   Terminal output of liqap.py's tick out of bound error 
| [Picture of Scanner output](./ScannerTerminalOut.png) | Terminal output of scanner.py
| [requirements.txt](./requirements.txt) |   lists all Python packages and their specific versions 
| [documentation.txt](./Documentation.txt) | Commands to activate enviornments and run scripts, and includes some personal notes to self 
| [package.json](./package.json) | Project configuration and dependencies 




