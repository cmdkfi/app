// SPDX-License-Identifier: MIT
pragma solidity 0.8.28;

import {MarketParams, IMorpho, IOracle, IChainlinkFeed, IUniswapV3Pool, IERC20} from "./Interfaces.sol";
import {CorridorMath as M} from "./CorridorMath.sol";

/// PRIME executor prototype (throwaway, 27-31 Oct 2026). Spec: docs/01-design-and-feasibility.md §3, §4.2, §4.8.
/// Permissionless and caller-scoped: every method acts only on msg.sender's Morpho position and sends tokens only to
/// msg.sender (or to the market's fixed pool as payment inside a swap). No admin, no upgrade, no native value.
/// Agent actions: reduce, migrate, exit. Owner paths: open, openFromStables, repay, close, setCorridor.
/// Every state-changing method takes a deadline (§4.1: every executor call the vault signs carries one).
contract Executor {
    // ---------------------------------------------------------------- types

    struct MarketConfig {
        MarketParams params;
        address pool; // the market's fixed Uniswap v3 pool (loan token / collateral token)
        address[2] feeds; // Chainlink feeds whose age gates new risk; address(0) = unused slot
        uint32[2] maxAges; // seconds
    }

    struct Corridor {
        uint64 target; // WAD; protective selling stops here
        uint64 act; // WAD; crossing it starts a protective session
        uint64 floor; // WAD; discretionary selling never goes below
        // WAD fraction of the collateral on the caller's agent-active markets in that token, per rolling 24 hours;
        // the smallest budget among those corridors applies. Discretionary sales only.
        uint64 sellBudget;
        bool migrateAllowed;
        bool exitAllowed;
        bool set; // a corridor is the owner's approval of the market
    }

    struct Sale {
        uint64 time;
        uint192 collateral;
    }

    enum Op {
        None,
        SellForRepay, // reduce: repay `assets`, sell collateral for exactly that
        ExitRepay, // exit: idle loan token first, then sell collateral for the rest
        PullRepay, // repay / close: pull exactly `assets` from the caller
        Migrate // flash loan: move shares and collateral between markets
    }

    // ---------------------------------------------------------------- constants

    uint256 internal constant WINDOW = 24 hours;
    uint256 internal constant SESSION = 24 hours;
    uint256 internal constant MAX_MOVES = 2; // per rolling WINDOW
    uint256 internal constant SALE_SLOTS = 16; // discretionary sales remembered per caller and collateral token (rolling WINDOW)
    uint256 internal constant DUST_DEBT = 1000; // loan units (0.001 USDC): a smaller remainder moves with a migrate
    uint160 internal constant MIN_SQRT_RATIO = 4295128739;
    uint160 internal constant MAX_SQRT_RATIO = 1461446703485210103287273052203988822378723970342;

    IMorpho public immutable MORPHO;
    uint256 public immutable COOLDOWN; // seconds between agent calls of one caller, across markets

    // ---------------------------------------------------------------- state

    mapping(bytes32 => MarketConfig) internal _config;
    bytes32[] public marketIds;

    mapping(address => mapping(bytes32 => Corridor)) public corridors;
    mapping(address => mapping(bytes32 => uint256)) public sessionStart; // 0 = no protective session
    mapping(address => mapping(bytes32 => bool)) public closedForAgent; // set by exit, cleared by open
    mapping(address => uint256) public lastAgentCall;
    mapping(address => uint64[MAX_MOVES]) internal _moves; // ring of move times
    mapping(address => uint256) internal _moveHead;
    mapping(address => mapping(address => Sale[SALE_SLOTS])) internal _sales; // ring of discretionary sales per caller and collateral token
    mapping(address => mapping(address => uint256)) internal _saleHead;

    bool transient _locked;
    address transient _pool; // the pool a swap callback may come from
    uint256 transient _collateralSold; // collateral paid into the pool by the current operation

    // ---------------------------------------------------------------- events and errors

    event CorridorSet(address indexed caller, bytes32 indexed market, Corridor corridor);
    event Opened(address indexed caller, bytes32 indexed market, uint256 collateralIn, uint256 borrowed, uint256 ltv);
    event Reduced(
        address indexed caller,
        bytes32 indexed market,
        uint256 repaid,
        uint256 collateralSold,
        uint256 ltvBefore,
        uint256 ltvAfter,
        bool protective
    );
    event Migrated(
        address indexed caller, bytes32 indexed from, bytes32 indexed to, uint256 fraction, uint256 debtMoved, uint256 collateralMoved
    );
    event Exited(address indexed caller, bytes32 indexed market, uint256 repaid, uint256 fromIdle, uint256 collateralSold, uint256 collateralOut);
    event SessionEnded(address indexed caller, bytes32 indexed market, uint256 ltv); // by a reduce that cannot sell
    event Repaid(address indexed caller, bytes32 indexed market, uint256 repaid);
    event Closed(address indexed caller, bytes32 indexed market, uint256 repaid, uint256 collateralOut);

    error Expired();
    error Reentrancy();
    error UnknownMarket();
    error UnapprovedMarket();
    error BadCorridor();
    error Cooldown();
    error BelowFloor();
    error SellBudget();
    error MoveLimit();
    error MigrateDisabled();
    error ExitDisabled();
    error MarketMismatch();
    error BadFraction();
    error NoDebt();
    error StaleFeed();
    error Slippage();
    error PartialFill();
    error AboveTarget();
    error NotMorpho();
    error NotPool();
    error MigrationMismatch();
    error BalanceChanged();
    error BadConfig();

    // ---------------------------------------------------------------- construction

    constructor(address morpho, uint256 cooldown, MarketConfig[] memory markets) {
        MORPHO = IMorpho(morpho);
        COOLDOWN = cooldown;
        for (uint256 i; i < markets.length; ++i) {
            MarketConfig memory c = markets[i];
            bytes32 id = keccak256(abi.encode(c.params));
            if (_config[id].pool != address(0)) revert BadConfig();
            (,,,, uint128 lastUpdate,) = IMorpho(morpho).market(id);
            if (lastUpdate == 0) revert BadConfig(); // market must exist on Morpho
            address t0 = IUniswapV3Pool(c.pool).token0();
            address t1 = IUniswapV3Pool(c.pool).token1();
            bool ok = (t0 == c.params.loanToken && t1 == c.params.collateralToken)
                || (t1 == c.params.loanToken && t0 == c.params.collateralToken);
            if (!ok) revert BadConfig();
            _config[id] = c;
            marketIds.push(id);
            // approvals only to Morpho; pools are paid by transfer inside their callback
            IERC20(c.params.loanToken).approve(morpho, type(uint256).max);
            IERC20(c.params.collateralToken).approve(morpho, type(uint256).max);
        }
    }

    modifier nonReentrant() {
        if (_locked) revert Reentrancy();
        _locked = true;
        _;
        _locked = false;
    }

    modifier before(uint256 deadline) {
        if (block.timestamp > deadline) revert Expired();
        _;
    }

    // ---------------------------------------------------------------- owner paths

    function setCorridor(bytes32 id, Corridor calldata c, uint256 deadline) external nonReentrant before(deadline) {
        MarketParams memory mp = _params(id);
        if (!M.corridorValid(c.floor, c.target, c.act, mp.lltv, c.sellBudget)) revert BadCorridor();
        Corridor memory s = c;
        s.set = true;
        corridors[msg.sender][id] = s;
        emit CorridorSet(msg.sender, id, s);
    }

    /// Supply `collateralIn` from the caller and borrow to the caller up to `targetLtv`.
    function open(bytes32 id, uint256 collateralIn, uint256 targetLtv, uint256 deadline)
        external
        nonReentrant
        before(deadline)
    {
        MarketParams memory mp = _params(id);
        uint256[2] memory snap = _snapshot(mp);
        _requireFresh(id);
        if (targetLtv >= mp.lltv) revert AboveTarget();
        MORPHO.accrueInterest(mp);
        if (collateralIn > 0) {
            _pull(mp.collateralToken, msg.sender, collateralIn);
            MORPHO.supplyCollateral(mp, collateralIn, msg.sender, "");
        }
        _borrowTo(id, mp, collateralIn, targetLtv);
        _check(mp, snap);
    }

    /// Swap `stablesIn` (the loan token) to collateral on the fixed pool at no worse than oracle minus 1%,
    /// supply it, and borrow to `targetLtv`.
    function openFromStables(bytes32 id, uint256 stablesIn, uint256 targetLtv, uint256 deadline)
        external
        nonReentrant
        before(deadline)
    {
        MarketParams memory mp = _params(id);
        uint256[2] memory snap = _snapshot(mp);
        _requireFresh(id);
        if (targetLtv >= mp.lltv) revert AboveTarget();
        MORPHO.accrueInterest(mp);
        _pull(mp.loanToken, msg.sender, stablesIn);
        uint256 price = IOracle(mp.oracle).price();
        uint256 received = _buyCollateral(id, mp, stablesIn, M.minCollateralOut(stablesIn, price));
        MORPHO.supplyCollateral(mp, received, msg.sender, "");
        _borrowTo(id, mp, received, targetLtv);
        _check(mp, snap);
    }

    /// Repay `amount` (all debt if `amount` covers it) from the caller's loan-token balance.
    function repay(bytes32 id, uint256 amount, uint256 deadline) external nonReentrant before(deadline) {
        MarketParams memory mp = _params(id);
        uint256[2] memory snap = _snapshot(mp);
        MORPHO.accrueInterest(mp);
        (uint256 shares, uint256 debt,) = _borrow(id, msg.sender);
        if (debt == 0) revert NoDebt();
        uint256 repaid;
        bytes memory cb = abi.encode(Op.PullRepay, msg.sender, id, uint256(0));
        if (amount >= debt) (repaid,) = MORPHO.repay(mp, 0, shares, msg.sender, cb);
        else (repaid,) = MORPHO.repay(mp, amount, 0, msg.sender, cb);
        emit Repaid(msg.sender, id, repaid);
        _check(mp, snap);
    }

    /// Full close: repay the exact remaining shares from the caller's loan token, withdraw all collateral to the caller.
    function close(bytes32 id, uint256 deadline) external nonReentrant before(deadline) {
        MarketParams memory mp = _params(id);
        uint256[2] memory snap = _snapshot(mp);
        MORPHO.accrueInterest(mp);
        (uint256 shares,,) = _borrow(id, msg.sender);
        uint256 repaid;
        if (shares > 0) {
            (repaid,) = MORPHO.repay(mp, 0, shares, msg.sender, abi.encode(Op.PullRepay, msg.sender, id, uint256(0)));
        }
        uint256 coll = _collateral(id, msg.sender);
        if (coll > 0) MORPHO.withdrawCollateral(mp, coll, msg.sender, msg.sender);
        delete sessionStart[msg.sender][id];
        emit Closed(msg.sender, id, repaid, coll);
        _check(mp, snap);
    }

    // ---------------------------------------------------------------- agent actions

    /// Sell collateral to repay `amount` of debt, clamped to the corridor (§4.2).
    function reduce(bytes32 id, uint256 amount, uint256 deadline) external nonReentrant before(deadline) {
        Corridor memory c = _agentCorridor(msg.sender, id);
        _cooldown(msg.sender);
        MarketParams memory mp = _params(id);
        uint256[2] memory snap = _snapshot(mp);
        MORPHO.accrueInterest(mp);
        (, uint256 debt,) = _borrow(id, msg.sender);
        if (debt == 0) revert NoDebt();
        uint256 price = IOracle(mp.oracle).price();
        uint256 coll = _collateral(id, msg.sender);
        uint256 v = M.value(coll, price);
        uint256 ltvBefore = M.ltv(debt, v);

        uint256 start = sessionStart[msg.sender][id];
        if (start != 0 && block.timestamp >= start + SESSION) start = 0; // session expired
        if (start == 0 && ltvBefore > c.act) start = block.timestamp; // crossing the act line starts one
        bool protective = start != 0 && ltvBefore > c.target + M.TARGET_TOLERANCE;

        uint256 maxAmount;
        if (protective) {
            sessionStart[msg.sender][id] = start;
            maxAmount = M.repayToLtv(debt, v, c.target);
            // maxAmount > 0: ltvBefore > target + TARGET_TOLERANCE makes repayToLtv at least 1 for any position
            // worth more than (WAD - target) / TARGET_TOLERANCE loan units (500, 0.0005 USDC, at a 50% target);
            // below that Morpho itself refuses a zero repay
        } else {
            bool hadSession = sessionStart[msg.sender][id] != 0; // live or lapsed
            delete sessionStart[msg.sender][id]; // a call that finds LTV within TARGET_TOLERANCE of target ends the session
            maxAmount = M.repayToLtv(debt, v, c.floor);
            if (maxAmount != 0) {
                (uint256 base, uint256 budget) = _activeCollateral(msg.sender, mp.collateralToken);
                uint256 budgetColl = M.remainingBudget(base, _soldInWindow(msg.sender, mp.collateralToken), budget);
                uint256 budgetAmount = M.worstCaseOut(budgetColl, price);
                if (budgetAmount == 0 && !hadSession) revert SellBudget();
                if (budgetAmount < maxAmount) maxAmount = budgetAmount;
            } else if (!hadSession) {
                revert BelowFloor();
            }
            if (maxAmount == 0) {
                // an observing call must end the session even when it cannot sell, or a recovery that no successful
                // call observes keeps the budget exemption alive
                lastAgentCall[msg.sender] = block.timestamp;
                emit SessionEnded(msg.sender, id, ltvBefore);
                return;
            }
        }
        if (amount > maxAmount) amount = maxAmount;

        _collateralSold = 0;
        MORPHO.repay(mp, amount, 0, msg.sender, abi.encode(Op.SellForRepay, msg.sender, id, price));
        uint256 sold = _collateralSold;
        if (!protective) _recordSale(msg.sender, mp.collateralToken, sold);
        lastAgentCall[msg.sender] = block.timestamp;

        (, uint256 debtAfter,) = _borrow(id, msg.sender);
        emit Reduced(msg.sender, id, amount, sold, ltvBefore, M.ltv(debtAfter, M.value(coll - sold, price)), protective);
        _check(mp, snap);
    }

    /// Move `fraction` (WAD) of the caller's debt and collateral from one approved market to another (§4.2).
    function migrate(bytes32 from, bytes32 to, uint256 fraction, uint256 deadline) external nonReentrant before(deadline) {
        if (from == to) revert MarketMismatch();
        Corridor memory cf = _agentCorridor(msg.sender, from);
        Corridor memory ct = _agentCorridor(msg.sender, to);
        if (!cf.migrateAllowed || !ct.migrateAllowed) revert MigrateDisabled();
        MarketParams memory a = _params(from);
        MarketParams memory b = _params(to);
        if (a.loanToken != b.loanToken || a.collateralToken != b.collateralToken) revert MarketMismatch();
        if (fraction == 0 || fraction > M.WAD) revert BadFraction();
        _cooldown(msg.sender);
        _recordMove(msg.sender);
        _requireFresh(to); // the source's feed is not checked: a broken source oracle is a reason to leave
        uint256[2] memory snap = _snapshot(a);

        MORPHO.accrueInterest(a);
        MORPHO.accrueInterest(b);
        (uint256 shares,,) = _borrow(from, msg.sender);
        if (shares == 0) revert NoDebt();
        uint256 coll = _collateral(from, msg.sender);
        (,, uint128 tba, uint128 tbs,,) = MORPHO.market(from);
        // Debt moved rounds up and collateral moved rounds down, so the part left behind is never less healthy.
        // A remainder whose debt is dust moves too: Morpho rounds dust shares up to a whole unit of debt, which the
        // dust collateral left with it cannot carry, and the source's withdrawCollateral would fail its health check.
        uint256 sharesMove = shares;
        uint256 collMove = coll;
        if (fraction < M.WAD) {
            sharesMove = (shares * fraction + M.WAD - 1) / M.WAD;
            collMove = coll * fraction / M.WAD;
            if (M.toAssetsUp(shares - sharesMove, tba, tbs) < DUST_DEBT) (sharesMove, collMove) = (shares, coll);
        }
        uint256 assets = M.toAssetsUp(sharesMove, tba, tbs);

        uint256 collToBefore = _collateral(to, msg.sender);
        (, uint256 debtToBefore,) = _borrow(to, msg.sender);

        // a move is a fresh position on the destination; if LTV there is still above the act line, the next reduce
        // starts a new session
        delete sessionStart[msg.sender][from];
        delete sessionStart[msg.sender][to];
        MORPHO.flashLoan(a.loanToken, assets, abi.encode(Op.Migrate, msg.sender, from, to, sharesMove, collMove));

        // checked after: collateral conserved exactly, debt conserved up to Morpho's rounding
        (, uint256 debtToAfter,) = _borrow(to, msg.sender);
        if (_collateral(to, msg.sender) != collToBefore + collMove) revert MigrationMismatch();
        if (debtToAfter > debtToBefore + assets + 1) revert MigrationMismatch();
        lastAgentCall[msg.sender] = block.timestamp;
        emit Migrated(msg.sender, from, to, fraction, assets, collMove);
        _check(a, snap);
    }

    /// Repay all debt (idle loan token first, then collateral sold at the oracle minimum) and withdraw all
    /// collateral to the caller. The market is then closed to the agent until the owner opens again.
    function exit(bytes32 id, uint256 deadline) external nonReentrant before(deadline) {
        Corridor memory c = _agentCorridor(msg.sender, id);
        if (!c.exitAllowed) revert ExitDisabled();
        _cooldown(msg.sender);
        MarketParams memory mp = _params(id);
        uint256[2] memory snap = _snapshot(mp);
        MORPHO.accrueInterest(mp);
        (uint256 shares,,) = _borrow(id, msg.sender);
        uint256 repaid;
        uint256 idleBefore = IERC20(mp.loanToken).balanceOf(msg.sender);
        _collateralSold = 0;
        if (shares > 0) {
            uint256 price = IOracle(mp.oracle).price();
            (repaid,) = MORPHO.repay(mp, 0, shares, msg.sender, abi.encode(Op.ExitRepay, msg.sender, id, price));
        }
        uint256 coll = _collateral(id, msg.sender);
        if (coll > 0) MORPHO.withdrawCollateral(mp, coll, msg.sender, msg.sender);
        closedForAgent[msg.sender][id] = true;
        delete sessionStart[msg.sender][id];
        lastAgentCall[msg.sender] = block.timestamp;
        emit Exited(msg.sender, id, repaid, idleBefore - IERC20(mp.loanToken).balanceOf(msg.sender), _collateralSold, coll);
        _check(mp, snap);
    }

    // ---------------------------------------------------------------- callbacks

    function onMorphoRepay(uint256 assets, bytes calldata data) external {
        if (msg.sender != address(MORPHO) || !_locked) revert NotMorpho();
        (Op op, address user, bytes32 id, uint256 price) = abi.decode(data, (Op, address, bytes32, uint256));
        MarketParams memory mp = _config[id].params;
        if (op == Op.SellForRepay) {
            _sellCollateralFor(id, mp, user, assets, price);
        } else if (op == Op.ExitRepay) {
            uint256 idle = _min(_min(IERC20(mp.loanToken).balanceOf(user), IERC20(mp.loanToken).allowance(user, address(this))), assets);
            if (idle > 0) _pull(mp.loanToken, user, idle);
            if (assets > idle) _sellCollateralFor(id, mp, user, assets - idle, price);
        } else if (op == Op.PullRepay) {
            _pull(mp.loanToken, user, assets);
        } else {
            revert NotMorpho();
        }
        // Morpho now pulls `assets` from this contract (approved in the constructor)
    }

    function onMorphoFlashLoan(uint256 assets, bytes calldata data) external {
        if (msg.sender != address(MORPHO) || !_locked) revert NotMorpho();
        (Op op, address user, bytes32 from, bytes32 to, uint256 sharesMove, uint256 collMove) =
            abi.decode(data, (Op, address, bytes32, bytes32, uint256, uint256));
        if (op != Op.Migrate) revert NotMorpho();
        MarketParams memory a = _config[from].params;
        MarketParams memory b = _config[to].params;
        (uint256 repaid,) = MORPHO.repay(a, 0, sharesMove, user, "");
        if (repaid != assets) revert MigrationMismatch();
        MORPHO.withdrawCollateral(a, collMove, user, address(this));
        MORPHO.supplyCollateral(b, collMove, user, "");
        MORPHO.borrow(b, assets, 0, user, address(this));
        // Morpho now pulls the flash loan back
    }

    /// Uniswap v3 swap callback: only the pool this contract is swapping on, only during that swap.
    function uniswapV3SwapCallback(int256 amount0Delta, int256 amount1Delta, bytes calldata data) external {
        address pool = _pool;
        if (pool == address(0) || msg.sender != pool) revert NotPool();
        (bool fromPosition, address user, bytes32 id, address payToken, uint256 maxIn) =
            abi.decode(data, (bool, address, bytes32, address, uint256));
        uint256 owed = uint256(amount0Delta > 0 ? amount0Delta : amount1Delta);
        if (fromPosition) {
            // pay the pool with the caller's collateral, withdrawn from Morpho straight to the pool
            if (owed > maxIn) revert Slippage();
            MORPHO.withdrawCollateral(_config[id].params, owed, user, pool);
            _collateralSold += owed;
        } else {
            _transfer(payToken, pool, owed);
        }
    }

    // ---------------------------------------------------------------- views

    function config(bytes32 id) external view returns (MarketConfig memory) {
        return _config[id];
    }

    function marketCount() external view returns (uint256) {
        return marketIds.length;
    }

    /// LTV (WAD) at Morpho's stored totals and the oracle price; does not accrue interest.
    function ltvOf(address user, bytes32 id) external view returns (uint256) {
        MarketParams memory mp = _params(id);
        (, uint256 debt,) = _borrow(id, user);
        return M.ltv(debt, M.value(_collateral(id, user), IOracle(mp.oracle).price()));
    }

    function soldInWindow(address user, address token) external view returns (uint256) {
        return _soldInWindow(user, token);
    }

    function movesInWindow(address user) external view returns (uint256 n) {
        uint64[MAX_MOVES] memory m = _moves[user];
        for (uint256 i; i < MAX_MOVES; ++i) {
            if (m[i] != 0 && block.timestamp < m[i] + WINDOW) ++n;
        }
    }

    // ---------------------------------------------------------------- internals

    function _params(bytes32 id) internal view returns (MarketParams memory mp) {
        mp = _config[id].params;
        if (mp.loanToken == address(0)) revert UnknownMarket();
    }

    function _agentCorridor(address user, bytes32 id) internal view returns (Corridor memory c) {
        c = corridors[user][id];
        if (!c.set || closedForAgent[user][id]) revert UnapprovedMarket();
    }

    function _cooldown(address user) internal view {
        uint256 last = lastAgentCall[user];
        if (last != 0 && block.timestamp < last + COOLDOWN) revert Cooldown();
    }

    function _requireFresh(bytes32 id) internal view {
        MarketConfig storage c = _config[id];
        for (uint256 i; i < 2; ++i) {
            address f = c.feeds[i];
            if (f == address(0)) continue;
            (, int256 answer,, uint256 updatedAt,) = IChainlinkFeed(f).latestRoundData();
            if (answer <= 0 || block.timestamp > updatedAt + c.maxAges[i]) revert StaleFeed();
        }
    }

    function _borrow(bytes32 id, address user) internal view returns (uint256 shares, uint256 debt, uint256 totalShares) {
        (, uint128 bs,) = MORPHO.position(id, user);
        (,, uint128 tba, uint128 tbs,,) = MORPHO.market(id);
        return (bs, M.toAssetsUp(bs, tba, tbs), tbs);
    }

    function _collateral(bytes32 id, address user) internal view returns (uint256) {
        (,, uint128 coll) = MORPHO.position(id, user);
        return coll;
    }

    /// Collateral of `user` in `token` on the agent-active markets (corridor set, not closed by exit, collateral
    /// held), and the smallest sell budget among their corridors (0 if there are none).
    function _activeCollateral(address user, address token) internal view returns (uint256 sum, uint256 minBudget) {
        minBudget = type(uint256).max;
        for (uint256 i; i < marketIds.length; ++i) {
            bytes32 id = marketIds[i];
            if (_config[id].params.collateralToken != token) continue;
            Corridor storage c = corridors[user][id];
            if (!c.set || closedForAgent[user][id]) continue;
            uint256 coll = _collateral(id, user);
            if (coll == 0) continue;
            sum += coll;
            if (c.sellBudget < minBudget) minBudget = c.sellBudget;
        }
        if (sum == 0) minBudget = 0;
    }

    function _borrowTo(bytes32 id, MarketParams memory mp, uint256 collateralIn, uint256 targetLtv) internal {
        uint256 price = IOracle(mp.oracle).price();
        uint256 v = M.value(_collateral(id, msg.sender), price);
        (, uint256 debt,) = _borrow(id, msg.sender);
        uint256 maxDebt = v * targetLtv / M.WAD;
        uint256 borrowed;
        // leave 2 units for Morpho's share rounding so the LTV after stays at or below target
        if (maxDebt > debt + 2) {
            borrowed = maxDebt - debt - 2;
            MORPHO.borrow(mp, borrowed, 0, msg.sender, msg.sender);
        }
        (, uint256 debtAfter,) = _borrow(id, msg.sender);
        uint256 ltvAfter = M.ltv(debtAfter, v);
        if (ltvAfter > targetLtv) revert AboveTarget();
        closedForAgent[msg.sender][id] = false; // the owner re-opens the market for the agent
        delete sessionStart[msg.sender][id]; // the owner's open is a fresh decision
        emit Opened(msg.sender, id, collateralIn, borrowed, ltvAfter);
    }

    /// Exact-output swap: receive exactly `out` loan tokens; pay collateral withdrawn from `user`'s position
    /// straight to the pool, at most `out` at oracle minus 1%.
    function _sellCollateralFor(bytes32 id, MarketParams memory mp, address user, uint256 out, uint256 price) internal {
        address pool = _config[id].pool;
        bool zeroForOne = IUniswapV3Pool(pool).token0() == mp.collateralToken;
        _pool = pool;
        (int256 a0, int256 a1) = IUniswapV3Pool(pool).swap(
            address(this),
            zeroForOne,
            -int256(out),
            zeroForOne ? MIN_SQRT_RATIO + 1 : MAX_SQRT_RATIO - 1,
            abi.encode(true, user, id, address(0), M.maxCollateralIn(out, price))
        );
        _pool = address(0);
        if (uint256(-(zeroForOne ? a1 : a0)) != out) revert PartialFill();
    }

    /// Exact-input swap of loan token held by this contract into collateral, at least `minOut`.
    function _buyCollateral(bytes32 id, MarketParams memory mp, uint256 amountIn, uint256 minOut)
        internal
        returns (uint256 received)
    {
        address pool = _config[id].pool;
        bool zeroForOne = IUniswapV3Pool(pool).token0() == mp.loanToken;
        _pool = pool;
        (int256 a0, int256 a1) = IUniswapV3Pool(pool).swap(
            address(this),
            zeroForOne,
            int256(amountIn),
            zeroForOne ? MIN_SQRT_RATIO + 1 : MAX_SQRT_RATIO - 1,
            abi.encode(false, address(0), id, mp.loanToken, uint256(0))
        );
        _pool = address(0);
        if (uint256(zeroForOne ? a0 : a1) != amountIn) revert PartialFill();
        received = uint256(-(zeroForOne ? a1 : a0));
        if (received < minOut) revert Slippage();
    }

    function _soldInWindow(address user, address token) internal view returns (uint256 sum) {
        Sale[SALE_SLOTS] storage s = _sales[user][token];
        for (uint256 i; i < SALE_SLOTS; ++i) {
            if (s[i].time != 0 && block.timestamp < uint256(s[i].time) + WINDOW) sum += s[i].collateral;
        }
    }

    function _recordSale(address user, address token, uint256 collateral) internal {
        uint256 h = _saleHead[user][token];
        Sale storage slot = _sales[user][token][h];
        // the oldest slot must have left the window, so the rolling sum stays exact
        if (slot.time != 0 && block.timestamp < uint256(slot.time) + WINDOW) revert SellBudget();
        slot.time = uint64(block.timestamp);
        slot.collateral = uint192(collateral);
        _saleHead[user][token] = (h + 1) % SALE_SLOTS;
    }

    function _recordMove(address user) internal {
        uint256 h = _moveHead[user];
        uint64 t = _moves[user][h];
        if (t != 0 && block.timestamp < uint256(t) + WINDOW) revert MoveLimit();
        _moves[user][h] = uint64(block.timestamp);
        _moveHead[user] = (h + 1) % MAX_MOVES;
    }

    function _snapshot(MarketParams memory mp) internal view returns (uint256[2] memory s) {
        s[0] = IERC20(mp.loanToken).balanceOf(address(this));
        s[1] = IERC20(mp.collateralToken).balanceOf(address(this));
    }

    /// The executor keeps nothing: its balances are unchanged by any call. (Compared with the start of the
    /// call rather than required to be zero, so a donation cannot block every later call.)
    function _check(MarketParams memory mp, uint256[2] memory s) internal view {
        if (IERC20(mp.loanToken).balanceOf(address(this)) != s[0]) revert BalanceChanged();
        if (IERC20(mp.collateralToken).balanceOf(address(this)) != s[1]) revert BalanceChanged();
    }

    function _pull(address token, address from, uint256 amount) internal {
        _call(token, abi.encodeCall(IERC20.transferFrom, (from, address(this), amount)));
    }

    function _transfer(address token, address to, uint256 amount) internal {
        _call(token, abi.encodeCall(IERC20.transfer, (to, amount)));
    }

    function _call(address token, bytes memory data) internal {
        (bool ok, bytes memory ret) = token.call(data);
        require(ok && (ret.length == 0 || abi.decode(ret, (bool))) && token.code.length > 0, "token transfer");
    }

    function _min(uint256 a, uint256 b) internal pure returns (uint256) {
        return a < b ? a : b;
    }
}
