// SPDX-License-Identifier: MIT
pragma solidity 0.8.28;

import {Script, console} from "forge-std/Script.sol";
import {Executor} from "../src/Executor.sol";
import {MarketParams, IMorpho, IOracle, IUniswapV3Pool} from "../src/Interfaces.sol";

interface IOracleFactory {
    function createMorphoChainlinkOracleV2(
        address baseVault,
        uint256 baseVaultConversionSample,
        address baseFeed1,
        address baseFeed2,
        uint256 baseTokenDecimals,
        address quoteVault,
        uint256 quoteVaultConversionSample,
        address quoteFeed1,
        address quoteFeed2,
        uint256 quoteTokenDecimals,
        bytes32 salt
    ) external returns (address);
}

interface IUniFactory {
    function createPool(address a, address b, uint24 fee) external returns (address);
    function getPool(address a, address b, uint24 fee) external view returns (address);
}

interface IMockOracle {
    function setPrice(uint256 p) external;
}

/// P1 deployment on Base Sepolia (or a local anvil fork of it):
/// 1. backup market: a second MorphoChainlinkOracleV2 on the live market's feeds, then createMarket (USDC/WETH, 0.86);
/// 2. demo market price set to the live oracle's price; demo pool dUSDC/WETH 0.01% created and initialised there;
/// 3. Executor with the three markets as constructor arguments;
/// 4. addresses, ids and transaction-free facts written to $DEPLOY_OUT (default deployments.json).
/// The key comes from the environment variable PK, set by script/deploy.sh from a file outside the repository.
/// Re-runs: set BACKUP_ORACLE to reuse an existing backup oracle (CREATE2 with a fixed salt cannot be repeated).
contract Deploy is Script {
    address constant MORPHO = 0xBBBBBbbBBb9cC5e90e3b3Af64bdAF62C37EEFFCb;
    address constant IRM = 0x46415998764C29aB2a25CbeA6254146D50D22687;
    address constant ORACLE_FACTORY = 0x2DC205F24BCb6B311E5cdf0745B0741648Aebd3d;
    address constant ETH_USD = 0x4aDC67696bA383F43DD60A9e78F2C97Fbbfc7cb1;
    address constant USDC_USD = 0xd30e2101a97dcbAeBCBC04F14C3f624E67A35165;
    address constant USDC = 0x036CbD53842c5426634e7929541eC2318f3dCF7e;
    address constant WETH = 0x4200000000000000000000000000000000000006;
    address constant LIVE_ORACLE = 0x7D0ebc71EBBEC6F215C2Ab818F9dB538A52f6a3E;
    address constant MOCK_ORACLE = 0x91Ec8d7177b876017773cf787B2caB0Fe6Ac6b2f;
    address constant DUSDC = 0xeb68Fd5bAAAfD4e46b43bAa7fEAb3216B87b10d5;
    address constant UNI_FACTORY = 0x4752ba5DBc23f44D87826276BF6Fd6b1C372aD24;
    address constant LIVE_POOL = 0x57183717A087d2fe3Ad890873877244c3B96156c;
    uint256 constant LLTV = 0.86e18;
    uint256 constant Q96 = 2 ** 96;

    function run() external {
        uint256 pk = vm.envUint("PK");
        string memory out = vm.envOr("DEPLOY_OUT", string("deployments.json"));
        uint256 cooldown = vm.envOr("COOLDOWN", uint256(60));
        address backupOracle = vm.envOr("BACKUP_ORACLE", address(0));

        MarketParams memory live = MarketParams(USDC, WETH, LIVE_ORACLE, IRM, LLTV);
        MarketParams memory demo = MarketParams(DUSDC, WETH, MOCK_ORACLE, IRM, LLTV);

        vm.startBroadcast(pk);
        if (backupOracle == address(0)) {
            backupOracle = IOracleFactory(ORACLE_FACTORY).createMorphoChainlinkOracleV2(
                address(0), 1, ETH_USD, address(0), 18, address(0), 1, USDC_USD, address(0), 6, keccak256("prime-backup")
            );
        }
        MarketParams memory backup = MarketParams(USDC, WETH, backupOracle, IRM, LLTV);
        bytes32 backupId = keccak256(abi.encode(backup));
        (,,,, uint128 lastUpdate,) = IMorpho(MORPHO).market(backupId);
        if (lastUpdate == 0) IMorpho(MORPHO).createMarket(backup);

        uint256 livePrice = IOracle(LIVE_ORACLE).price();
        IMockOracle(MOCK_ORACLE).setPrice(livePrice);
        address demoPool = IUniFactory(UNI_FACTORY).getPool(WETH, DUSDC, 100);
        if (demoPool == address(0)) {
            demoPool = IUniFactory(UNI_FACTORY).createPool(WETH, DUSDC, 100);
            IUniswapV3Pool(demoPool).initialize(_sqrtX96(demoPool, WETH, livePrice));
        }

        Executor.MarketConfig[] memory c = new Executor.MarketConfig[](3);
        address[2] memory feeds = [ETH_USD, USDC_USD];
        uint32[2] memory ages = [uint32(1200), uint32(86400)];
        c[0] = Executor.MarketConfig(live, LIVE_POOL, feeds, ages);
        c[1] = Executor.MarketConfig(backup, LIVE_POOL, feeds, ages);
        c[2] = Executor.MarketConfig(demo, demoPool, [address(0), address(0)], [uint32(0), uint32(0)]);
        Executor exec = new Executor(MORPHO, cooldown, c);
        vm.stopBroadcast();

        string memory j = "deploy";
        vm.serializeUint(j, "chainId", block.chainid);
        vm.serializeUint(j, "block", block.number);
        vm.serializeAddress(j, "executor", address(exec));
        vm.serializeUint(j, "cooldownSeconds", cooldown);
        vm.serializeAddress(j, "morpho", MORPHO);
        vm.serializeAddress(j, "backupOracle", backupOracle);
        vm.serializeAddress(j, "mockOracle", MOCK_ORACLE);
        vm.serializeAddress(j, "liveOracle", LIVE_ORACLE);
        vm.serializeAddress(j, "livePool", LIVE_POOL);
        vm.serializeAddress(j, "demoPool", demoPool);
        vm.serializeAddress(j, "usdc", USDC);
        vm.serializeAddress(j, "weth", WETH);
        vm.serializeAddress(j, "dusdc", DUSDC);
        vm.serializeBytes32(j, "liveMarketId", keccak256(abi.encode(live)));
        vm.serializeBytes32(j, "backupMarketId", backupId);
        string memory json = vm.serializeBytes32(j, "demoMarketId", keccak256(abi.encode(demo)));
        vm.writeJson(json, out);
        console.log("executor", address(exec));
        console.log("backup oracle", backupOracle);
        console.log("demo pool", demoPool);
    }

    /// sqrtPriceX96 of `pool` for Morpho oracle price `o` (loan units per collateral unit, 1e36 scale).
    function _sqrtX96(address pool, address collateral, uint256 o) internal view returns (uint160) {
        if (IUniswapV3Pool(pool).token0() == collateral) return uint160(_sqrt(o * 1e12) * Q96 / 1e24);
        return uint160(Q96 * 1e24 / _sqrt(o * 1e12));
    }

    function _sqrt(uint256 x) internal pure returns (uint256 z) {
        if (x == 0) return 0;
        z = x;
        uint256 y = (x + 1) / 2;
        while (y < z) {
            z = y;
            y = (x / y + y) / 2;
        }
    }
}
