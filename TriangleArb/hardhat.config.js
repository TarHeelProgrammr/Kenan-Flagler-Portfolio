require("@nomicfoundation/hardhat-ethers");
// Uncomment the next line if you plan to verify contracts on an explorer
// require("@nomiclabs/hardhat-etherscan");
require("dotenv").config();

module.exports = {
  solidity: {
    compilers: [
      { version: "0.8.26" },
      { version: "0.7.6" },
    ],
    overrides: {
      "contracts/TriFlash.sol": {
        version: "0.7.6",
        settings: {
          optimizer: {
            enabled: true,
            runs: 200,
          },
        },
      },
      "@uniswap/v3-periphery/contracts/libraries/PoolAddress.sol": {
        version: "0.7.6", // Ensure all dependencies are compatible
      },
    },
  },
  networks: {
    base: {
      url: process.env.BASE_RPC,
      accounts: [process.env.PRIVATE_KEY],
    },
  },
};