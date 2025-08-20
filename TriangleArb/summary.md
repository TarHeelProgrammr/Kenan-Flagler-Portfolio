hardhat enviorment  similair to the manual flash loan 

E Scanner uses the same three pools every time and has a working log 

Scanner.py tries to dynmaically build triangular route canidates, but likely has some sort of filering unexpected profit alphas likely because 
it doesn't incorporate the V3 math 

liqap.py attempts to incorporate V3 math, but we are getting tick out of bound erros so we can't get the terminal to display/

pools.json has all the pools we were trying to dynamically pool from. 

scanner_log.txt is the output we want to screen shot and some of the terminal,  screen shot whats wrong with liqap.py in terminal ('out of bounds tick error')

Tri flashloan attemepted to execute E scanners arbitrage logic, but we never deployed this smart contract. 
