// File: hardhat.config.js
require("@nomicfoundation/hardhat-toolbox");
require("dotenv").config();

module.exports = {
  solidity: {
    version: "0.8.20",
    settings: {
      optimizer: {
        enabled: true,
        runs: 200,
      },
      viaIR: true,
    },
  },
  networks: {
    basemainnet: {
      url: process.env.BASE_RPC_URL, // e.g., https://mainnet.base.org
      accounts: [process.env.PRIVATE_KEY],
      chainId: 8453, // Update this if needed for Base mainnet
    },
  },
};
