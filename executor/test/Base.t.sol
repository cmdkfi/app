// SPDX-License-Identifier: MIT
pragma solidity 0.8.28;

import {Test} from "forge-std/Test.sol";
import {Executor} from "../src/Executor.sol";
import {MarketParams, IMorpho, IOracle, IChainlinkFeed, IUniswapV3Pool, IERC20} from "../src/Interfaces.sol";
import {TestToken, TestOracle} from "./TestToken.sol";

interface IMockOracle {
    function setPrice(uint256 p) external;
    function owner() external view returns (address);
}

interface IMockUSDC {
    function mint(address to, uint256 amount) external;
}

interface IUniFactory {
    function createPool(address a, address b, uint24 fee) external returns (address);
    function getPool(address a, address b, uint24 fee) external view returns (address);
}

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

/// Base Sepolia fork fixture: live market (real), backup market (created here the same way P1's deploy creates it),
/// demo market (real, mock oracle) with a dUSDC/WETH pool created here. Deep full-range liquidity is added to the
/// live pool and the demo pool so price impact is negligible unless a test moves a pool on purpose.
/// alt market (created here): dUSDC loan against TKN, a second collateral token, with its own oracle and pool.
abstract contract ForkBase is Test {
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
    bytes32 constant LIVE_ID = 0xa25ac5171b7e500b82653d3750b91bb44e2409c32ca3c9e80b0db077e8a4535c;
    bytes32 constant DEMO_ID = 0x55872b53dba6147b6badf9d4277bc166dfb7d5f8ac8d491da093960dbef6d585;
    uint256 constant FORK_BLOCK = 50578525;
    uint256 constant LLTV = 0.86e18;
    uint256 constant COOLDOWN = 60;
    uint256 constant Q96 = 2 ** 96;
    int24 constant MIN_TICK = -887272;
    int24 constant MAX_TICK = 887272;

    IMorpho morpho = IMorpho(MORPHO);
    Executor exec;
    MarketParams live;
    MarketParams backup;
    MarketParams demo;
    bytes32 BACKUP_ID;
    address demoPool;
    MarketParams alt;
    bytes32 ALT_ID;
    address altPool;
    address altOracle;
    address TKN;

    address alice = makeAddr("alice");
    address bob = makeAddr("bob");
    address carol = makeAddr("carol");
    address supplier = makeAddr("supplier");

    int256 ethUsdAnswer;
    int256 usdcUsdAnswer;

    function setUp() public virtual {
        vm.createSelectFork(vm.envOr("BASE_SEPOLIA_RPC", string("https://base-sepolia-rpc.publicnode.com")), FORK_BLOCK);
        (, ethUsdAnswer,,,) = IChainlinkFeed(ETH_USD).latestRoundData();
        (, usdcUsdAnswer,,,) = IChainlinkFeed(USDC_USD).latestRoundData();
        _fresh();

        live = MarketParams(USDC, WETH, LIVE_ORACLE, IRM, LLTV);
        demo = MarketParams(DUSDC, WETH, MOCK_ORACLE, IRM, LLTV);
        assertEq(keccak256(abi.encode(live)), LIVE_ID, "live id");
        assertEq(keccak256(abi.encode(demo)), DEMO_ID, "demo id");

        // backup market: a second Chainlink oracle instance on the same feeds, same tokens, LLTV 0.86
        address backupOracle = IOracleFactory(ORACLE_FACTORY).createMorphoChainlinkOracleV2(
            address(0), 1, ETH_USD, address(0), 18, address(0), 1, USDC_USD, address(0), 6, keccak256("prime-backup")
        );
        backup = MarketParams(USDC, WETH, backupOracle, IRM, LLTV);
        morpho.createMarket(backup);
        BACKUP_ID = keccak256(abi.encode(backup));

        // demo pool: dUSDC/WETH 0.01%, started at the mock oracle price
        _setMockPrice(2400e24);
        demoPool = IUniFactory(UNI_FACTORY).createPool(WETH, DUSDC, 100);
        IUniswapV3Pool(demoPool).initialize(_sqrtX96(demoPool, WETH, 2400e24));
        IUniswapV3Pool(demoPool).mint(address(this), MIN_TICK, MAX_TICK, 5e16, "");
        // live pool: bring it to the live oracle and add deep liquidity
        IUniswapV3Pool(LIVE_POOL).mint(address(this), MIN_TICK, MAX_TICK, 5e16, "");
        _alignPool(LIVE_POOL, WETH, IOracle(LIVE_ORACLE).price(), 10_000);

        // alt market: dUSDC against TKN at $100 (same oracle scale as the demo oracle), TKN/dUSDC 0.01% pool
        TKN = address(new TestToken("Test Token", "TKN"));
        altOracle = address(new TestOracle());
        TestOracle(altOracle).setPrice(100e24);
        alt = MarketParams(DUSDC, TKN, altOracle, IRM, LLTV);
        morpho.createMarket(alt);
        ALT_ID = keccak256(abi.encode(alt));
        altPool = IUniFactory(UNI_FACTORY).createPool(TKN, DUSDC, 100);
        IUniswapV3Pool(altPool).initialize(_sqrtX96(altPool, TKN, 100e24));
        IUniswapV3Pool(altPool).mint(address(this), MIN_TICK, MAX_TICK, 5e16, "");
        _alignPool(altPool, TKN, 100e24, 10_000);

        // lenders
        _supply(live, 20_000e6);
        _supply(backup, 20_000e6);
        _supply(demo, 1_000_000e6);
        _supply(alt, 1_000_000e6);

        exec = new Executor(MORPHO, COOLDOWN, _configs(backupOracle));
        _user(alice);
        _user(bob);
    }

    function _configs(address) internal view returns (Executor.MarketConfig[] memory c) {
        c = new Executor.MarketConfig[](4);
        address[2] memory feeds = [ETH_USD, USDC_USD];
        uint32[2] memory ages = [uint32(1200), uint32(86400)];
        c[0] = Executor.MarketConfig(live, LIVE_POOL, feeds, ages);
        c[1] = Executor.MarketConfig(backup, LIVE_POOL, feeds, ages);
        c[2] = Executor.MarketConfig(demo, demoPool, [address(0), address(0)], [uint32(0), uint32(0)]);
        c[3] = Executor.MarketConfig(alt, altPool, [address(0), address(0)], [uint32(0), uint32(0)]);
    }

    // ------------------------------------------------------------ helpers

    function _user(address u) internal {
        _fund(WETH, u, 10e18);
        _fund(TKN, u, 100e18);
        vm.startPrank(u);
        morpho.setAuthorization(address(exec), true);
        IERC20(WETH).approve(address(exec), type(uint256).max);
        IERC20(USDC).approve(address(exec), type(uint256).max);
        IERC20(DUSDC).approve(address(exec), type(uint256).max);
        IERC20(TKN).approve(address(exec), type(uint256).max);
        vm.stopPrank();
    }

    function _supply(MarketParams memory mp, uint256 amount) internal {
        _fund(mp.loanToken, supplier, amount);
        vm.startPrank(supplier);
        IERC20(mp.loanToken).approve(MORPHO, amount);
        morpho.supply(mp, amount, 0, supplier, "");
        vm.stopPrank();
    }

    function _fund(address token, address to, uint256 amount) internal {
        if (token == DUSDC) IMockUSDC(DUSDC).mint(to, amount);
        else if (token == TKN) TestToken(TKN).mint(to, amount);
        else deal(token, to, IERC20(token).balanceOf(to) + amount);
    }

    /// Chainlink feeds answer as at the fork block but are updated "now".
    function _fresh() internal {
        _mockFeed(ETH_USD, ethUsdAnswer, block.timestamp);
        _mockFeed(USDC_USD, usdcUsdAnswer, block.timestamp);
    }

    function _mockFeed(address feed, int256 answer, uint256 updatedAt) internal {
        vm.mockCall(
            feed,
            abi.encodeWithSelector(IChainlinkFeed.latestRoundData.selector),
            abi.encode(uint80(1), answer, updatedAt, updatedAt, uint80(1))
        );
    }

    function _warp(uint256 s) internal {
        vm.warp(block.timestamp + s);
        _fresh();
    }

    function _setMockPrice(uint256 p) internal {
        vm.prank(IMockOracle(MOCK_ORACLE).owner());
        IMockOracle(MOCK_ORACLE).setPrice(p);
    }

    /// Set the demo oracle to `usd` dollars per ETH and move the demo pool to the same price.
    function _demoPrice(uint256 usd) internal {
        _setMockPrice(usd * 1e24);
        _alignPool(demoPool, WETH, usd * 1e24, 10_000);
    }

    /// Move `pool` to `bps`/10000 of the oracle price `o` (loan units per collateral unit, 1e36 scale).
    function _alignPool(address pool, address collateral, uint256 o, uint256 bps) internal {
        uint160 target = _sqrtX96(pool, collateral, o * bps / 10_000);
        (uint160 cur,,,,,,) = IUniswapV3Pool(pool).slot0();
        if (cur == target) return;
        IUniswapV3Pool(pool).swap(address(this), target < cur, type(int128).max, target, "");
    }

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

    function uniswapV3MintCallback(uint256 a0, uint256 a1, bytes calldata) external {
        if (a0 > 0) _fund(IUniswapV3Pool(msg.sender).token0(), msg.sender, a0);
        if (a1 > 0) _fund(IUniswapV3Pool(msg.sender).token1(), msg.sender, a1);
    }

    function uniswapV3SwapCallback(int256 a0, int256 a1, bytes calldata) external {
        if (a0 > 0) _fund(IUniswapV3Pool(msg.sender).token0(), msg.sender, uint256(a0));
        if (a1 > 0) _fund(IUniswapV3Pool(msg.sender).token1(), msg.sender, uint256(a1));
    }

    function _corridor(uint64 target, uint64 act, uint64 floor, uint64 budget, bool mig, bool ex)
        internal
        pure
        returns (Executor.Corridor memory)
    {
        return Executor.Corridor(target, act, floor, budget, mig, ex, false);
    }

    function _default() internal pure returns (Executor.Corridor memory) {
        return _corridor(0.5e18, 0.7e18, 0.3e18, 0.2e18, true, true);
    }

    function _dl() internal view returns (uint256) {
        return block.timestamp + 600;
    }

    function _setCorridor(address u, bytes32 id, Executor.Corridor memory c) internal {
        vm.prank(u);
        exec.setCorridor(id, c, _dl());
    }

    function _open(address u, bytes32 id, uint256 coll, uint256 ltv) internal {
        vm.prank(u);
        exec.open(id, coll, ltv, _dl());
    }

    function _pos(address u, bytes32 id) internal view returns (uint256 coll, uint256 debt) {
        (, uint128 bs, uint128 c) = morpho.position(id, u);
        (,, uint128 tba, uint128 tbs,,) = morpho.market(id);
        coll = c;
        debt = (uint256(bs) * (uint256(tba) + 1) + uint256(tbs) + 1e6 - 1) / (uint256(tbs) + 1e6);
    }

    function _assertExecutorEmpty() internal view {
        assertEq(IERC20(WETH).balanceOf(address(exec)), 0, "exec WETH");
        assertEq(IERC20(USDC).balanceOf(address(exec)), 0, "exec USDC");
        assertEq(IERC20(DUSDC).balanceOf(address(exec)), 0, "exec dUSDC");
        assertEq(IERC20(TKN).balanceOf(address(exec)), 0, "exec TKN");
    }
}
