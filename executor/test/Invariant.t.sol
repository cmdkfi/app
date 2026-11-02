// SPDX-License-Identifier: MIT
pragma solidity 0.8.28;

import {console} from "forge-std/console.sol";
import {ForkBase} from "./Base.t.sol";
import {Executor} from "../src/Executor.sol";
import {MarketParams, IOracle, IERC20} from "../src/Interfaces.sol";
import {TestOracle} from "./TestToken.sol";

/// Handler-driven invariant test of the corridor rules (design §4.2) on the Base Sepolia fork.
/// The test contract is its own handler: the fuzzer calls only the h_* functions (targetSelector), so the fixture
/// helpers and the Uniswap callbacks of ForkBase stay available. Each handler pranks alice or bob, calls the executor
/// inside try/catch, and checks the postconditions of a successful call; a broken rule sets `violated` with a reason,
/// which invariant_noViolation reports. Timestamps are read with vm.getBlockTimestamp() (via-IR trap).
/// Starting state (bias, so agent actions have something to act on from the first call): both actors hold the
/// corridor target 40%, act 55%, floor 25%, budget 20%, migrate and exit on, on all four markets; alice has positions
/// on live, backup and demo, bob on live, backup and alt, at 45% LTV (a 20% price drop crosses the act line).
/// Close and exit wipe positions, so h_open prefers a market where the actor has none. Other seed biases are
/// described on the handlers; coverage per handler is logged by invariant_callSummary.
/// forge-config: default.invariant.runs = 64
/// forge-config: default.invariant.depth = 25
/// forge-config: default.invariant.fail-on-revert = false
contract ExecutorInvariant is ForkBase {
    uint256 constant WINDOW = 24 hours;
    uint256 constant SALE_SLOTS = 16;
    uint256 constant MAX_MOVES = 2;

    uint256 constant F_SET = 0;
    uint256 constant F_OPEN = 1;
    uint256 constant F_REDUCE = 2;
    uint256 constant F_MIGRATE = 3;
    uint256 constant F_EXIT = 4;
    uint256 constant F_REPAY = 5;
    uint256 constant F_CLOSE = 6;
    uint256 constant F_WARP = 7;
    uint256 constant F_DEMO = 8;
    uint256 constant F_ALT = 9;
    uint256 constant NF = 10;

    bytes32[4] ids; // live, backup, demo, alt
    address[4] tokens; // USDC, dUSDC, WETH, TKN

    // ghost counters: calls and successful executor calls per handler
    uint256[NF] calls;
    uint256[NF] oks;
    uint256 protectiveOk;
    uint256 discretionaryOk;
    uint256 unequalBudgetOk; // discretionary reduces with unequal budgets among the active corridors
    uint256 invalidCorridorTried;

    // ghost logs
    mapping(address => mapping(address => uint256[])) saleLog; // actor => collateral token => discretionary sale times
    mapping(address => uint256[]) moveLog; // actor => migrate times
    mapping(address => uint256) lastAgentOk; // actor => time of the last successful reduce, migrate or exit

    bool violated;
    string reason;

    function setUp() public override {
        super.setUp();
        // The fork runs as an OP-stack chain, where every raw call pays an L1 data fee: the setup calls come from the
        // default sender and the handler calls from one fixed sender, so both get ETH.
        address fuzzer = makeAddr("fuzzer");
        vm.deal(msg.sender, 1 ether);
        vm.deal(fuzzer, 1 ether);
        targetSender(fuzzer);
        ids = [LIVE_ID, BACKUP_ID, DEMO_ID, ALT_ID];
        tokens = [USDC, DUSDC, WETH, TKN];
        Executor.Corridor memory c0 = _corridor(0.4e18, 0.55e18, 0.25e18, 0.2e18, true, true);
        for (uint256 i; i < 4; ++i) {
            _setCorridor(alice, ids[i], c0);
            _setCorridor(bob, ids[i], c0);
        }
        _open(alice, LIVE_ID, 1e18, 0.45e18);
        _open(alice, BACKUP_ID, 1e18, 0.45e18);
        _open(alice, DEMO_ID, 1e18, 0.45e18);
        _open(bob, LIVE_ID, 1e18, 0.45e18);
        _open(bob, BACKUP_ID, 1e18, 0.45e18);
        _open(bob, ALT_ID, 10e18, 0.45e18);

        bytes4[] memory s = new bytes4[](NF);
        s[F_SET] = this.h_setCorridor.selector;
        s[F_OPEN] = this.h_open.selector;
        s[F_REDUCE] = this.h_reduce.selector;
        s[F_MIGRATE] = this.h_migrate.selector;
        s[F_EXIT] = this.h_exit.selector;
        s[F_REPAY] = this.h_repay.selector;
        s[F_CLOSE] = this.h_close.selector;
        s[F_WARP] = this.h_warp.selector;
        s[F_DEMO] = this.h_demoPrice.selector;
        s[F_ALT] = this.h_altPrice.selector;
        targetContract(address(this));
        targetSelector(FuzzSelector({addr: address(this), selectors: s}));
    }

    // ------------------------------------------------------------ owner paths

    /// A valid corridor, or in about 1 of 8 calls one that breaks exactly one rule (must revert BadCorridor).
    /// The budget is drawn per call, so an actor's corridors in one token drift apart and h_reduce checks the minimum.
    function h_setCorridor(
        uint256 actorSeed,
        uint256 marketSeed,
        uint256 target,
        uint256 act,
        uint256 floor,
        uint256 budget,
        bool mig,
        bool ex
    ) external {
        address u = _actor(actorSeed);
        uint256 i = marketSeed % 4;
        act = bound(act, 0.1e18, LLTV - 1);
        target = bound(target, 0.01e18, act - 0.001e18 - 1);
        floor = bound(floor, 0, target - 1);
        budget = bound(budget, 0, 1e18);
        uint256 h = uint256(keccak256(abi.encode(marketSeed, target))); // the fuzzer favours small raw seeds
        bool invalid = h % 8 == 0;
        if (invalid) {
            invalidCorridorTried++;
            uint256 k = (h >> 8) % 4;
            if (k == 0) floor = target;
            else if (k == 1) act = target + 0.001e18;
            else if (k == 2) act = LLTV;
            else budget = 1e18 + 1;
        }
        Executor.Corridor memory c = _corridor(uint64(target), uint64(act), uint64(floor), uint64(budget), mig, ex);
        uint256[4] memory ob = _balances(_other(u));
        vm.prank(u);
        try exec.setCorridor(ids[i], c, _dl()) {
            _count(F_SET, true);
            _expect(!invalid, "setCorridor accepted an invalid corridor");
            Executor.Corridor memory s = _corr(u, i);
            _expect(s.set && s.target == target && s.act == act && s.floor == floor, "corridor not stored");
            _expect(s.sellBudget == budget && s.migrateAllowed == mig && s.exitAllowed == ex, "corridor not stored");
        } catch (bytes memory err) {
            _count(F_SET, false);
            _expect(invalid, "setCorridor refused a valid corridor");
            _expect(bytes4(err) == Executor.BadCorridor.selector, "invalid corridor refused without BadCorridor");
        }
        _after(u, ob);
    }

    /// Collateral bounded to what the actor holds; LTV below the corridor's act line (0.7 without a corridor).
    /// In 3 of 4 calls the market is the first one from marketSeed % 4 where the actor has no position.
    function h_open(uint256 actorSeed, uint256 marketSeed, uint256 coll, uint256 ltv) external {
        address u = _actor(actorSeed);
        uint256 i = marketSeed % 4;
        if (uint256(keccak256(abi.encode(marketSeed, u))) % 4 != 0) i = _pickEmpty(u, marketSeed);
        uint256 cap = i == 3 ? 20e18 : 2e18;
        uint256 bal = IERC20(_collToken(i)).balanceOf(u);
        if (bal < cap) cap = bal;
        coll = bound(coll, 0, cap);
        Executor.Corridor memory c = _corr(u, i);
        ltv = bound(ltv, 0.05e18, c.set ? c.act - 0.01e18 : 0.7e18);
        uint256[4] memory ob = _balances(_other(u));
        vm.prank(u);
        try exec.open(ids[i], coll, ltv, _dl()) {
            _count(F_OPEN, true);
            _expect(!exec.closedForAgent(u, ids[i]), "open left the market closed for the agent");
            _expect(exec.ltvOf(u, ids[i]) <= ltv, "open borrowed above the requested LTV");
            _expect(exec.sessionStart(u, ids[i]) == 0, "open left a session open");
        } catch {
            _count(F_OPEN, false);
        }
        _after(u, ob);
    }

    function h_repay(uint256 actorSeed, uint256 marketSeed, uint256 amount) external {
        address u = _actor(actorSeed);
        uint256 i = _pickDebt(u, marketSeed);
        uint256 bal = IERC20(_loanToken(i)).balanceOf(u);
        amount = bound(amount, 1, bal > 1 ? bal : 1);
        uint256[4] memory ob = _balances(_other(u));
        vm.prank(u);
        try exec.repay(ids[i], amount, _dl()) {
            _count(F_REPAY, true);
        } catch {
            _count(F_REPAY, false);
        }
        _after(u, ob);
    }

    /// The actor is topped up with the loan token to cover the debt, so close can succeed after exits used it.
    function h_close(uint256 actorSeed, uint256 marketSeed) external {
        address u = _actor(actorSeed);
        uint256 i = _pickDebt(u, marketSeed);
        morpho.accrueInterest(_mp(i));
        (, uint256 debt) = _pos(u, ids[i]);
        uint256 bal = IERC20(_loanToken(i)).balanceOf(u);
        if (bal < debt + 1) _fund(_loanToken(i), u, debt + 1 - bal);
        uint256[4] memory ob = _balances(_other(u));
        vm.prank(u);
        try exec.close(ids[i], _dl()) {
            _count(F_CLOSE, true);
            (uint256 c, uint256 d) = _pos(u, ids[i]);
            _expect(c == 0 && d == 0 && _shares(u, i) == 0, "close left a position");
            _expect(exec.sessionStart(u, ids[i]) == 0, "close left a session open");
        } catch {
            _count(F_CLOSE, false);
        }
        _after(u, ob);
    }

    // ------------------------------------------------------------ agent actions

    /// The market is, in half the calls, the agent-ready one with the highest LTV (so positions pushed over the act
    /// line by a price drop get protective sales), otherwise the first agent-ready one from marketSeed % 4.
    function h_reduce(uint256 actorSeed, uint256 marketSeed, uint256 amount) external {
        address u = _actor(actorSeed);
        _maybeWaitCooldown(u, actorSeed);
        uint256 i = _pickAgent(u, marketSeed);
        if (uint256(keccak256(abi.encode(marketSeed, u))) % 2 == 0) i = _pickRiskiest(u, i);
        address ct = _collToken(i);
        amount = bound(amount, 1, 3000e6);
        uint256 soldBefore = exec.soldInWindow(u, ct);
        (uint256 collBefore,) = _pos(u, ids[i]);
        uint256[4] memory ob = _balances(_other(u));
        uint256[4] memory mb = _balances(u);
        vm.prank(u);
        try exec.reduce(ids[i], amount, _dl()) {
            _count(F_REDUCE, true);
            _agentOk(u);
            _expect(_same(mb, _balances(u)), "reduce moved the caller's wallet balances");
            (uint256 collAfter,) = _pos(u, ids[i]);
            uint256 ltvAfter = exec.ltvOf(u, ids[i]);
            uint256 sold = exec.soldInWindow(u, ct);
            Executor.Corridor memory c = _corr(u, i);
            if (exec.sessionStart(u, ids[i]) != 0) {
                protectiveOk++;
                _expect(ltvAfter >= c.target, "protective reduce went below target");
                _expect(sold == soldBefore, "protective reduce changed soldInWindow");
            } else {
                discretionaryOk++;
                _expect(ltvAfter >= c.floor, "discretionary reduce went below floor");
                _expect(sold == soldBefore + (collBefore - collAfter), "discretionary sale not recorded exactly");
                uint256 limit = _minBudget(u, ct) * (_activeColl(u, ct) + sold) / 1e18;
                _expect(sold <= limit, "discretionary sales above the sell budget");
                if (_unequalBudgets(u, ct)) unequalBudgetOk++;
                saleLog[u][ct].push(vm.getBlockTimestamp());
                _expect(_inWindow(saleLog[u][ct]) <= SALE_SLOTS, "more than 16 discretionary sales in 24 h");
            }
        } catch {
            _count(F_REDUCE, false);
        }
        _after(u, ob);
    }

    /// `from` is a live/backup position the agent may act on in 7 of 8 calls (any agent-ready market otherwise);
    /// `to` is the sibling market in 7 of 8 calls (live <-> backup; demo <-> alt is a token mismatch), any market
    /// otherwise. The fraction is 0 or above 100% (BadFraction) in 1 of 16 calls and 1% to 100% otherwise: below about
    /// 1e-6 Morpho refuses the move (debt rounds up to a whole unit against collateral that rounds down).
    function h_migrate(uint256 actorSeed, uint256 fromSeed, uint256 toSeed, uint256 fraction) external {
        address u = _actor(actorSeed);
        _maybeWaitCooldown(u, actorSeed);
        uint256 hf = uint256(keccak256(abi.encode(fromSeed, u)));
        uint256 ht = uint256(keccak256(abi.encode(toSeed, u)));
        uint256 from = _pickAgent(u, fromSeed);
        if (hf % 8 != 0) {
            uint256 first = (hf >> 8) % 2;
            if (_agentReady(u, first)) from = first;
            else if (_agentReady(u, 1 - first)) from = 1 - first;
        }
        uint256 to = ht % 8 == 0 ? (ht >> 8) % 4 : _sibling(from);
        if ((ht >> 16) % 16 == 0) fraction = (ht >> 24) % 2 == 0 ? 0 : bound(fraction, 1e18 + 1, 2e18);
        else fraction = bound(fraction, 0.01e18, 1e18);
        if (from != to) {
            morpho.accrueInterest(_mp(from));
            morpho.accrueInterest(_mp(to));
        }
        address ct = _collToken(from);
        uint256 collBefore = _totalColl(u, ct);
        uint256 debtBefore = _debt(u, from) + _debt(u, to);
        uint256[4] memory ob = _balances(_other(u));
        uint256[4] memory mb = _balances(u);
        vm.prank(u);
        try exec.migrate(ids[from], ids[to], fraction, _dl()) {
            _count(F_MIGRATE, true);
            _agentOk(u);
            _expect(_collToken(to) == ct && _loanToken(to) == _loanToken(from), "migrate between different tokens");
            _expect(_corr(u, from).migrateAllowed && _corr(u, to).migrateAllowed, "migrate without migrateAllowed");
            _expect(_totalColl(u, ct) == collBefore, "migrate changed the total collateral");
            uint256 debtAfter = _debt(u, from) + _debt(u, to);
            uint256 diff = debtAfter > debtBefore ? debtAfter - debtBefore : debtBefore - debtAfter;
            _expect(diff <= 2, "migrate changed the total debt by more than 2 units");
            _expect(_same(mb, _balances(u)), "migrate moved the caller's wallet balances");
            moveLog[u].push(vm.getBlockTimestamp());
            _expect(_inWindow(moveLog[u]) <= MAX_MOVES, "more than 2 migrates in 24 h");
            _expect(exec.sessionStart(u, ids[from]) == 0, "migrate left a session open on the source");
            _expect(exec.sessionStart(u, ids[to]) == 0, "migrate left a session open on the destination");
        } catch {
            _count(F_MIGRATE, false);
        }
        _after(u, ob);
    }

    function h_exit(uint256 actorSeed, uint256 marketSeed) external {
        address u = _actor(actorSeed);
        _maybeWaitCooldown(u, actorSeed);
        uint256 i = _pickAgent(u, marketSeed);
        uint256[4] memory ob = _balances(_other(u));
        vm.prank(u);
        try exec.exit(ids[i], _dl()) {
            _count(F_EXIT, true);
            _agentOk(u);
            _expect(_corr(u, i).exitAllowed, "exit without exitAllowed");
            _expect(exec.closedForAgent(u, ids[i]), "exit did not close the market for the agent");
            (uint256 c,) = _pos(u, ids[i]);
            _expect(c == 0 && _shares(u, i) == 0, "exit left a position");
        } catch {
            _count(F_EXIT, false);
        }
        _after(u, ob);
    }

    // ------------------------------------------------------------ environment

    /// Half the calls move 1 s to 5 min (cooldown edges), half 1 s to 12 h. _warp refreshes the mocked feeds.
    function h_warp(uint256 secs) external {
        _count(F_WARP, true);
        if (secs % 2 == 0) _warp(bound(secs >> 1, 1, 300));
        else _warp(bound(secs >> 1, 1, 12 hours));
    }

    /// Half the calls set any price from 800 to 4,000 dollars; half drop the current price by 5% to 40% (not below
    /// 800), so positions cross the act line without going under water and protective sales get exercised.
    function h_demoPrice(uint256 usd) external {
        _count(F_DEMO, true);
        if (usd % 2 == 0) usd = bound(usd >> 1, 800, 4000);
        else usd = _drop(IOracle(MOCK_ORACLE).price() / 1e24, usd >> 1, 800);
        _demoPrice(usd);
    }

    /// Same split as h_demoPrice, 40 to 250 dollars.
    function h_altPrice(uint256 usd) external {
        _count(F_ALT, true);
        if (usd % 2 == 0) usd = bound(usd >> 1, 40, 250);
        else usd = _drop(TestOracle(altOracle).price() / 1e24, usd >> 1, 40);
        TestOracle(altOracle).setPrice(usd * 1e24);
        _alignPool(altPool, TKN, usd * 1e24, 10_000);
    }

    // ------------------------------------------------------------ invariants

    function invariant_executorHoldsNothing() public view {
        _assertExecutorEmpty();
    }

    function invariant_noViolation() public view {
        assertFalse(violated, reason);
        assertLe(exec.movesInWindow(alice), MAX_MOVES, "alice movesInWindow");
        assertLe(exec.movesInWindow(bob), MAX_MOVES, "bob movesInWindow");
    }

    function invariant_callSummary() public view {
        string[NF] memory names = [
            string("setCorridor"),
            "open",
            "reduce",
            "migrate",
            "exit",
            "repay",
            "close",
            "warp",
            "demoPrice",
            "altPrice"
        ];
        console.log("handler      calls  ok  reverted");
        for (uint256 f; f < NF; ++f) {
            console.log(
                string.concat(names[f], ": ", vm.toString(calls[f]), " ", vm.toString(oks[f])), calls[f] - oks[f]
            );
        }
        console.log("reduce protective ok", protectiveOk);
        console.log("reduce discretionary ok", discretionaryOk);
        console.log("reduce discretionary ok, unequal active budgets", unequalBudgetOk);
        console.log("invalid corridors tried", invalidCorridorTried);
    }

    // ------------------------------------------------------------ helpers

    function _expect(bool ok, string memory why) internal {
        if (!ok && !violated) {
            violated = true;
            reason = why;
        }
    }

    function _count(uint256 f, bool ok) internal {
        calls[f]++;
        if (ok) oks[f]++;
    }

    /// After every actor call: the executor holds nothing and the other actor's wallet did not move.
    function _after(address u, uint256[4] memory otherBefore) internal {
        _expect(_execEmpty(), "executor holds tokens after a call");
        _expect(_same(otherBefore, _balances(_other(u))), "a call moved the other actor's balances");
    }

    /// Two successful agent calls of one actor are at least COOLDOWN apart.
    function _agentOk(address u) internal {
        uint256 t = vm.getBlockTimestamp();
        uint256 last = lastAgentOk[u];
        _expect(last == 0 || t >= last + COOLDOWN, "two agent calls within the cooldown");
        _expect(exec.lastAgentCall(u) == t, "lastAgentCall not updated");
        lastAgentOk[u] = t;
    }

    /// In 3 of 4 agent calls, wait out a running cooldown first (otherwise most agent calls hit Cooldown); the
    /// fourth call goes ahead as it is, so calls inside the cooldown keep being tried.
    function _maybeWaitCooldown(address u, uint256 seed) internal {
        if (uint256(keccak256(abi.encode(seed, u))) % 4 == 0) return;
        uint256 last = exec.lastAgentCall(u);
        uint256 t = vm.getBlockTimestamp();
        if (last != 0 && t < last + COOLDOWN) _warp(last + COOLDOWN - t);
    }

    /// `cur` lowered by 5% to 40%, not below `min`.
    function _drop(uint256 cur, uint256 seed, uint256 min) internal pure returns (uint256 p) {
        p = cur * (95 - seed % 36) / 100;
        if (p < min) p = min;
    }

    function _inWindow(uint256[] storage log) internal view returns (uint256 n) {
        uint256 t = vm.getBlockTimestamp();
        for (uint256 k = log.length; k > 0; --k) {
            if (log[k - 1] + WINDOW <= t) break;
            ++n;
        }
    }

    function _actor(uint256 seed) internal view returns (address) {
        return seed % 2 == 0 ? alice : bob;
    }

    function _other(address u) internal view returns (address) {
        return u == alice ? bob : alice;
    }

    function _mp(uint256 i) internal view returns (MarketParams memory) {
        if (i == 0) return live;
        if (i == 1) return backup;
        if (i == 2) return demo;
        return alt;
    }

    function _collToken(uint256 i) internal view returns (address) {
        return i == 3 ? TKN : WETH;
    }

    function _loanToken(uint256 i) internal pure returns (address) {
        return i < 2 ? USDC : DUSDC;
    }

    function _sibling(uint256 i) internal pure returns (uint256) {
        return i ^ 1; // live <-> backup, demo <-> alt
    }

    function _corr(address u, uint256 i) internal view returns (Executor.Corridor memory c) {
        (c.target, c.act, c.floor, c.sellBudget, c.migrateAllowed, c.exitAllowed, c.set) = exec.corridors(u, ids[i]);
    }

    function _shares(address u, uint256 i) internal view returns (uint256) {
        (, uint128 bs,) = morpho.position(ids[i], u);
        return bs;
    }

    function _debt(address u, uint256 i) internal view returns (uint256 d) {
        (, d) = _pos(u, ids[i]);
    }

    function _agentReady(address u, uint256 i) internal view returns (bool) {
        return _corr(u, i).set && !exec.closedForAgent(u, ids[i]) && _shares(u, i) > 0;
    }

    /// The first market from seed % 4 on that the agent may act on and that has debt; seed % 4 if none.
    function _pickAgent(address u, uint256 seed) internal view returns (uint256) {
        uint256 start = seed % 4;
        for (uint256 k; k < 4; ++k) {
            uint256 j = (start + k) % 4;
            if (_agentReady(u, j)) return j;
        }
        return start;
    }

    /// The agent-ready market with the highest LTV; `dflt` if none is ready.
    function _pickRiskiest(address u, uint256 dflt) internal view returns (uint256 best) {
        best = dflt;
        uint256 top;
        for (uint256 j; j < 4; ++j) {
            if (!_agentReady(u, j)) continue;
            uint256 l = exec.ltvOf(u, ids[j]);
            if (l > top) (top, best) = (l, j);
        }
    }

    function _pickEmpty(address u, uint256 seed) internal view returns (uint256) {
        uint256 start = seed % 4;
        for (uint256 k; k < 4; ++k) {
            uint256 j = (start + k) % 4;
            (uint256 c,) = _pos(u, ids[j]);
            if (c == 0 && _shares(u, j) == 0) return j;
        }
        return start;
    }

    function _pickDebt(address u, uint256 seed) internal view returns (uint256) {
        uint256 start = seed % 4;
        for (uint256 k; k < 4; ++k) {
            uint256 j = (start + k) % 4;
            if (_shares(u, j) > 0) return j;
        }
        return start;
    }

    function _totalColl(address u, address token) internal view returns (uint256 sum) {
        for (uint256 i; i < 4; ++i) {
            if (_collToken(i) == token) {
                (uint256 c,) = _pos(u, ids[i]);
                sum += c;
            }
        }
    }

    /// Agent-active: the actor's corridor is set, the market is not closed by exit, and the actor holds collateral.
    function _active(address u, uint256 i) internal view returns (bool) {
        (uint256 c,) = _pos(u, ids[i]);
        return _corr(u, i).set && !exec.closedForAgent(u, ids[i]) && c > 0;
    }

    /// Collateral in this token on the actor's agent-active markets: the base of the sell budget.
    function _activeColl(address u, address token) internal view returns (uint256 sum) {
        for (uint256 i; i < 4; ++i) {
            if (_collToken(i) != token || !_active(u, i)) continue;
            (uint256 c,) = _pos(u, ids[i]);
            sum += c;
        }
    }

    /// The smallest sell budget among the actor's agent-active corridors in this token; 0 if none is active.
    function _minBudget(address u, address token) internal view returns (uint256 m) {
        m = type(uint256).max;
        for (uint256 i; i < 4; ++i) {
            if (_collToken(i) != token || !_active(u, i)) continue;
            uint256 b = _corr(u, i).sellBudget;
            if (b < m) m = b;
        }
        if (m == type(uint256).max) m = 0;
    }

    function _unequalBudgets(address u, address token) internal view returns (bool) {
        uint256 m = _minBudget(u, token);
        for (uint256 i; i < 4; ++i) {
            if (_collToken(i) == token && _active(u, i) && _corr(u, i).sellBudget != m) return true;
        }
        return false;
    }

    function _balances(address u) internal view returns (uint256[4] memory b) {
        for (uint256 k; k < 4; ++k) {
            b[k] = IERC20(tokens[k]).balanceOf(u);
        }
    }

    function _same(uint256[4] memory a, uint256[4] memory b) internal pure returns (bool) {
        return a[0] == b[0] && a[1] == b[1] && a[2] == b[2] && a[3] == b[3];
    }

    function _execEmpty() internal view returns (bool) {
        for (uint256 k; k < 4; ++k) {
            if (IERC20(tokens[k]).balanceOf(address(exec)) != 0) return false;
        }
        return true;
    }
}
