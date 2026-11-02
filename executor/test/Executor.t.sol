// SPDX-License-Identifier: MIT
pragma solidity 0.8.28;

import {ForkBase} from "./Base.t.sol";
import {Executor} from "../src/Executor.sol";
import {MarketParams, IOracle, IERC20} from "../src/Interfaces.sol";
import {CorridorMath as M} from "../src/CorridorMath.sol";

contract ExecutorForkTest is ForkBase {
    // ------------------------------------------------------------ open

    function test_open_at_50pct_live() public {
        _setCorridor(alice, LIVE_ID, _default());
        _open(alice, LIVE_ID, 1e18, 0.5e18);
        uint256 ltv = exec.ltvOf(alice, LIVE_ID);
        assertLe(ltv, 0.5e18);
        assertGe(ltv, 0.4999e18);
        (uint256 coll, uint256 debt) = _pos(alice, LIVE_ID);
        assertEq(coll, 1e18);
        assertApproxEqAbs(IERC20(USDC).balanceOf(alice), debt, 1, "borrowed USDC sent to the caller");
        _assertExecutorEmpty();
    }

    function test_open_at_50pct_demo() public {
        _open(alice, DEMO_ID, 1e18, 0.5e18);
        uint256 ltv = exec.ltvOf(alice, DEMO_ID);
        assertLe(ltv, 0.5e18);
        assertGe(ltv, 0.4999e18);
        assertEq(IERC20(DUSDC).balanceOf(alice), 1200e6 - 2, "2 units left for Morpho's share rounding");
        _assertExecutorEmpty();
    }

    function test_open_refused_on_stale_feed() public {
        _mockFeed(ETH_USD, ethUsdAnswer, block.timestamp - 1201);
        vm.prank(alice);
        vm.expectRevert(Executor.StaleFeed.selector);
        exec.open(LIVE_ID, 1e18, 0.5e18, _dl());
        // exactly at the heartbeat is still fresh
        _mockFeed(ETH_USD, ethUsdAnswer, block.timestamp - 1200);
        _open(alice, LIVE_ID, 1e18, 0.5e18);
    }

    function test_open_refuses_target_at_or_above_lltv() public {
        vm.prank(alice);
        vm.expectRevert(Executor.AboveTarget.selector);
        exec.open(DEMO_ID, 1e18, LLTV, _dl());
    }

    function test_openFromStables() public {
        _fund(DUSDC, alice, 1200e6);
        vm.prank(alice);
        exec.openFromStables(DEMO_ID, 1200e6, 0.4e18, _dl());
        (uint256 coll, uint256 debt) = _pos(alice, DEMO_ID);
        assertApproxEqRel(coll, 0.5e18, 0.001e18, "about 0.5 WETH bought at $2,400");
        assertGe(coll, 0.495e18, "oracle minus 1% minimum");
        assertApproxEqRel(debt, 480e6, 0.002e18);
        assertLe(exec.ltvOf(alice, DEMO_ID), 0.4e18);
        _assertExecutorEmpty();
    }

    function test_openFromStables_refused_when_pool_3pct_above_oracle() public {
        _fund(DUSDC, alice, 1200e6);
        _alignPool(demoPool, WETH, 2400e24, 10_300); // WETH 3% dearer in the pool than at the oracle
        vm.prank(alice);
        vm.expectRevert(Executor.Slippage.selector);
        exec.openFromStables(DEMO_ID, 1200e6, 0.4e18, _dl());
    }

    function test_openFromStables_refused_on_stale_feed() public {
        _fund(USDC, alice, 1000e6);
        _mockFeed(USDC_USD, usdcUsdAnswer, block.timestamp - 86401);
        vm.prank(alice);
        vm.expectRevert(Executor.StaleFeed.selector);
        exec.openFromStables(LIVE_ID, 1000e6, 0.4e18, _dl());
    }

    // ------------------------------------------------------------ reduce: protective session

    function test_reduce_protective_session_sells_to_target_in_slices() public {
        // budget 0: protective selling above the act line is not budget-limited
        _setCorridor(alice, DEMO_ID, _corridor(0.5e18, 0.7e18, 0.3e18, 0, true, true));
        _open(alice, DEMO_ID, 1e18, 0.5e18);
        _demoPrice(1600); // LTV 75%: above the act line
        assertGt(exec.ltvOf(alice, DEMO_ID), 0.7e18);

        uint256 last = exec.ltvOf(alice, DEMO_ID);
        for (uint256 i; i < 3; ++i) {
            vm.prank(alice);
            exec.reduce(DEMO_ID, 100e6, _dl());
            uint256 l = exec.ltvOf(alice, DEMO_ID);
            assertLt(l, last, "each slice lowers LTV");
            last = l;
            assertGt(exec.sessionStart(alice, DEMO_ID), 0, "session open");
            _warp(COOLDOWN);
        }
        assertLt(last, 0.7e18, "third slice is below the act line, session continues");
        assertEq(exec.soldInWindow(alice, WETH), 0, "protective sales do not use the budget");

        // ask for far more than needed: clamped so LTV stops at target
        vm.prank(alice);
        exec.reduce(DEMO_ID, 10_000e6, _dl());
        uint256 atTarget = exec.ltvOf(alice, DEMO_ID);
        assertGe(atTarget, 0.5e18, "never below target");
        assertLe(atTarget, 0.5005e18, "within pool slippage of target");

        // price recovers a little: LTV now below target; with no budget the call cannot sell, but it ends the session
        _warp(COOLDOWN);
        _demoPrice(1620);
        assertLt(exec.ltvOf(alice, DEMO_ID), 0.5e18);
        _demoReduceEndsSession(1e6);
        _assertExecutorEmpty();
    }

    function test_reduce_session_never_goes_below_target() public {
        _setCorridor(alice, DEMO_ID, _corridor(0.5e18, 0.7e18, 0.3e18, 0, true, true));
        _open(alice, DEMO_ID, 1e18, 0.5e18);
        _demoPrice(1600);
        for (uint256 i; i < 5; ++i) {
            vm.prank(alice);
            try exec.reduce(DEMO_ID, 10_000e6, _dl()) {} catch {}
            assertGe(exec.ltvOf(alice, DEMO_ID), 0.5e18);
            _warp(COOLDOWN);
        }
    }

    /// A fill below the oracle leaves LTV a hair above target. That is within TARGET_TOLERANCE (0.1 pp), so the next
    /// call ends the session and is discretionary: with budget 0 it sells nothing instead of selling dust.
    function test_reduce_session_ends_within_tolerance_of_target() public {
        _setCorridor(alice, DEMO_ID, _corridor(0.5e18, 0.7e18, 0.3e18, 0, true, true));
        _open(alice, DEMO_ID, 1e18, 0.5e18);
        _demoPrice(1600);
        vm.prank(alice);
        exec.reduce(DEMO_ID, 10_000e6, _dl());
        uint256 l1 = exec.ltvOf(alice, DEMO_ID);
        (uint256 c1,) = _pos(alice, DEMO_ID);
        assertGt(l1, 0.5e18, "a hair above target");
        assertLe(l1, 0.5e18 + 0.001e18, "within the tolerance");
        assertGt(exec.sessionStart(alice, DEMO_ID), 0, "session open after the clamped slice");

        _warp(COOLDOWN);
        _demoReduceEndsSession(10_000e6); // session ended: discretionary, budget 0, nothing sold

        // no session stored now: the same call is refused for the budget
        _warp(COOLDOWN);
        vm.prank(alice);
        vm.expectRevert(Executor.SellBudget.selector);
        exec.reduce(DEMO_ID, 10_000e6, _dl());
        (uint256 c2,) = _pos(alice, DEMO_ID);
        assertEq(c2, c1, "no collateral sold");

        // with a budget the same call is a discretionary sale
        _setCorridor(alice, DEMO_ID, _corridor(0.5e18, 0.7e18, 0.3e18, 0.2e18, true, true));
        vm.prank(alice);
        exec.reduce(DEMO_ID, 1e6, _dl());
        assertEq(exec.sessionStart(alice, DEMO_ID), 0, "no session");
        assertGt(exec.soldInWindow(alice, WETH), 0, "the sale was discretionary");
    }

    /// LTV above target + TARGET_TOLERANCE keeps the session protective; inside the tolerance it ends.
    function test_reduce_session_continues_above_tolerance() public {
        _setCorridor(alice, DEMO_ID, _corridor(0.5e18, 0.7e18, 0.3e18, 0, true, true));
        _open(alice, DEMO_ID, 1e18, 0.5e18);
        _demoPrice(1600); // LTV 75%
        vm.prank(alice);
        exec.reduce(DEMO_ID, 100e6, _dl()); // starts the session
        uint256 started = exec.sessionStart(alice, DEMO_ID);
        assertGt(started, 0, "session open");
        assertGt(exec.ltvOf(alice, DEMO_ID), 0.7e18, "still above the act line");

        // LTV 50.2%: between target + tolerance and act
        _warp(COOLDOWN);
        _demoPriceForLtv(0.502e18);
        uint256 l = exec.ltvOf(alice, DEMO_ID);
        assertGt(l, 0.5e18 + 0.001e18, "above the tolerance");
        assertLt(l, 0.7e18, "below the act line");
        (uint256 c0,) = _pos(alice, DEMO_ID);
        vm.prank(alice);
        exec.reduce(DEMO_ID, 10_000e6, _dl());
        (uint256 c1,) = _pos(alice, DEMO_ID);
        assertLt(c1, c0, "protective sale with budget 0");
        assertEq(exec.soldInWindow(alice, WETH), 0, "protective sales do not use the budget");
        assertEq(exec.sessionStart(alice, DEMO_ID), started, "session continues");
        assertGe(exec.ltvOf(alice, DEMO_ID), 0.5e18, "not below target");

        // LTV 50.05%: inside the tolerance, the call ends the session and is discretionary: budget 0, nothing sold
        _warp(COOLDOWN);
        _demoPriceForLtv(0.5005e18);
        l = exec.ltvOf(alice, DEMO_ID);
        assertGt(l, 0.5e18, "above target");
        assertLe(l, 0.5e18 + 0.001e18, "within the tolerance");
        _demoReduceEndsSession(10_000e6);
    }

    /// Set the demo oracle (and pool) to the price at which alice's demo position has LTV `l` (WAD).
    /// Mock price scale: loan units per collateral unit times 1e36 (e.g. $2,400 = 2400e24).
    function _demoPriceForLtv(uint256 l) internal {
        (uint256 coll, uint256 debt) = _pos(alice, DEMO_ID);
        uint256 p = debt * 1e36 * 1e18 / (coll * l);
        _setMockPrice(p);
        _alignPool(demoPool, WETH, p, 10_000);
    }

    function test_reduce_session_ends_after_24h() public {
        _setCorridor(alice, DEMO_ID, _corridor(0.5e18, 0.7e18, 0.3e18, 0, true, true));
        _open(alice, DEMO_ID, 1e18, 0.5e18);
        _demoPrice(1600);
        vm.prank(alice);
        exec.reduce(DEMO_ID, 200e6, _dl()); // 75% -> about 71%... session started
        _demoPrice(1700); // now below the act line, above target
        _warp(24 hours);
        _demoReduceEndsSession(10e6); // the session has lapsed: discretionary, budget 0; the stored start is cleared
    }

    /// alice's reduce on demo that cannot sell (budget or floor) but finds a stored session: no revert, the session
    /// ends, SessionEnded carries the LTV the call saw, nothing is sold and the cooldown starts.
    function _demoReduceEndsSession(uint256 amount) internal {
        assertGt(exec.sessionStart(alice, DEMO_ID), 0, "a session is stored");
        morpho.accrueInterest(demo); // the call accrues first; accruing here makes ltvOf the LTV the call sees
        uint256 l = exec.ltvOf(alice, DEMO_ID);
        (uint256 c0, uint256 d0) = _pos(alice, DEMO_ID);
        uint256 s0 = exec.soldInWindow(alice, WETH);
        vm.expectEmit(true, true, true, true, address(exec));
        emit Executor.SessionEnded(alice, DEMO_ID, l);
        vm.prank(alice);
        exec.reduce(DEMO_ID, amount, _dl());
        (uint256 c1, uint256 d1) = _pos(alice, DEMO_ID);
        assertEq(c1, c0, "no collateral sold");
        assertEq(d1, d0, "no debt repaid");
        assertEq(exec.soldInWindow(alice, WETH), s0, "soldInWindow unchanged");
        assertEq(exec.sessionStart(alice, DEMO_ID), 0, "session ended");
        assertEq(exec.lastAgentCall(alice), vm.getBlockTimestamp(), "an agent call: the cooldown starts");
        _assertExecutorEmpty();
    }

    /// Review Low 1, scenario E: after a session sells to target the price recovers fully and an observing call that
    /// cannot sell (budget 0) still ends the session. Ten hours later a drop that stays below the act line is
    /// discretionary and refused for the budget; a drop above the act line still gets a protective sale.
    function test_reduce_observing_call_ends_the_session_without_selling() public {
        _setCorridor(alice, DEMO_ID, _corridor(0.5e18, 0.7e18, 0.3e18, 0, true, true));
        _open(alice, DEMO_ID, 1e18, 0.5e18);
        _demoPrice(1600); // LTV 75%: above the act line
        vm.prank(alice);
        exec.reduce(DEMO_ID, 10_000e6, _dl());
        assertGt(exec.sessionStart(alice, DEMO_ID), 0, "session open after the protective sale");
        assertLe(exec.ltvOf(alice, DEMO_ID), 0.5005e18, "sold to target");

        // full recovery, LTV about 33%: the observing call sells nothing and ends the session
        _warp(COOLDOWN);
        _demoPriceForLtv(0.33e18);
        _demoReduceEndsSession(1e6);

        // ten hours later, LTV about 53%: below the act line, no session, budget 0: refused
        _warp(10 hours);
        _demoPriceForLtv(0.53e18);
        uint256 l = exec.ltvOf(alice, DEMO_ID);
        assertGt(l, 0.5e18 + 0.001e18, "above target + tolerance");
        assertLt(l, 0.7e18, "below the act line");
        (uint256 c0,) = _pos(alice, DEMO_ID);
        vm.prank(alice);
        vm.expectRevert(Executor.SellBudget.selector);
        exec.reduce(DEMO_ID, 10_000e6, _dl());
        (uint256 c1,) = _pos(alice, DEMO_ID);
        assertEq(c1, c0, "no collateral sold");
        assertEq(exec.sessionStart(alice, DEMO_ID), 0, "no session");

        // the honest path: a drop above the act line gets a protective sale and opens a new session
        _demoPriceForLtv(0.75e18);
        uint256 now_ = vm.getBlockTimestamp(); // not block.timestamp: via-IR may re-read it at the use
        vm.prank(alice);
        exec.reduce(DEMO_ID, 10_000e6, _dl());
        (uint256 c2,) = _pos(alice, DEMO_ID);
        assertLt(c2, c1, "protective sale with budget 0");
        assertEq(exec.sessionStart(alice, DEMO_ID), now_, "new session");
        assertEq(exec.soldInWindow(alice, WETH), 0, "protective sales do not use the budget");
        assertGe(exec.ltvOf(alice, DEMO_ID), 0.5e18, "not below target");
        _assertExecutorEmpty();
    }

    /// A session call that finds LTV below the floor cannot sell either: it ends the session the same way. Without a
    /// stored session the same call is refused BelowFloor.
    function test_reduce_below_floor_with_session_ends_it_without_selling() public {
        _setCorridor(alice, DEMO_ID, _corridor(0.5e18, 0.7e18, 0.45e18, 0.2e18, true, true));
        _open(alice, DEMO_ID, 1e18, 0.5e18);
        _demoPrice(1600); // LTV 75%: above the act line
        vm.prank(alice);
        exec.reduce(DEMO_ID, 10_000e6, _dl());
        assertGt(exec.sessionStart(alice, DEMO_ID), 0, "session open after the protective sale");
        assertLe(exec.ltvOf(alice, DEMO_ID), 0.5005e18, "sold to target");

        // recovery to LTV about 40%, below the 45% floor
        _warp(COOLDOWN);
        _demoPriceForLtv(0.4e18);
        assertLt(exec.ltvOf(alice, DEMO_ID), 0.45e18, "below the floor");
        _demoReduceEndsSession(10_000e6);

        _warp(COOLDOWN);
        (uint256 c0,) = _pos(alice, DEMO_ID);
        vm.prank(alice);
        vm.expectRevert(Executor.BelowFloor.selector);
        exec.reduce(DEMO_ID, 10_000e6, _dl());
        (uint256 c1,) = _pos(alice, DEMO_ID);
        assertEq(c1, c0, "no collateral sold");
        assertEq(exec.sessionStart(alice, DEMO_ID), 0, "no session");
    }

    /// The owner's open ends the session: a re-lever below the act line two hours after a protective sale is not
    /// sold back to target by the agent without the budget.
    function test_open_ends_the_session() public {
        _setCorridor(alice, DEMO_ID, _corridor(0.5e18, 0.7e18, 0.3e18, 0, true, true));
        _open(alice, DEMO_ID, 1e18, 0.5e18);
        _demoPrice(1600); // LTV 75%: above the act line
        vm.prank(alice);
        exec.reduce(DEMO_ID, 10_000e6, _dl());
        assertGt(exec.sessionStart(alice, DEMO_ID), 0, "session open after the protective sale");
        assertLe(exec.ltvOf(alice, DEMO_ID), 0.5005e18, "sold to target");

        // two hours later the price recovers and the owner re-levers below the act line
        _warp(2 hours);
        _demoPrice(2000);
        _open(alice, DEMO_ID, 0, 0.65e18);
        uint256 l = exec.ltvOf(alice, DEMO_ID);
        assertGt(l, 0.5e18 + 0.001e18, "above target + tolerance");
        assertLt(l, 0.7e18, "below the act line");
        assertEq(exec.sessionStart(alice, DEMO_ID), 0, "open ended the session");

        (uint256 c0,) = _pos(alice, DEMO_ID);
        vm.prank(alice);
        vm.expectRevert(Executor.SellBudget.selector); // discretionary, budget 0
        exec.reduce(DEMO_ID, 10_000e6, _dl());
        (uint256 c1,) = _pos(alice, DEMO_ID);
        assertEq(c1, c0, "no collateral sold");

        // with a 20% budget the same call is a discretionary sale within the budget
        _setCorridor(alice, DEMO_ID, _corridor(0.5e18, 0.7e18, 0.3e18, 0.2e18, true, true));
        vm.prank(alice);
        exec.reduce(DEMO_ID, 10_000e6, _dl());
        uint256 sold = exec.soldInWindow(alice, WETH);
        assertGt(sold, 0, "the sale was discretionary");
        assertLe(sold, c0 * 0.2e18 / 1e18, "within the 20% budget");
        assertEq(exec.sessionStart(alice, DEMO_ID), 0, "no session");
        _assertExecutorEmpty();
    }

    // ------------------------------------------------------------ reduce: discretionary

    function test_reduce_discretionary_inside_budget() public {
        _setCorridor(alice, DEMO_ID, _default()); // budget 20%
        _open(alice, DEMO_ID, 1e18, 0.5e18); // LTV 50% < act 70%
        vm.prank(alice);
        exec.reduce(DEMO_ID, 100e6, _dl());
        uint256 sold = exec.soldInWindow(alice, WETH);
        assertApproxEqRel(sold, uint256(100e6) * 1e36 / 2400e24, 0.001e18, "about 100/2400 WETH sold");
        assertEq(exec.sessionStart(alice, DEMO_ID), 0, "no session below the act line");

        // ask for everything: clamped to the remaining budget (20% of collateral at window start)
        for (uint256 i; i < 6; ++i) {
            _warp(COOLDOWN);
            vm.prank(alice);
            try exec.reduce(DEMO_ID, 10_000e6, _dl()) {} catch {}
            assertLe(exec.soldInWindow(alice, WETH), 0.2e18, "never more than the budget");
            assertGt(exec.ltvOf(alice, DEMO_ID), 0.3e18, "never below the floor");
        }
        assertGe(exec.soldInWindow(alice, WETH), 0.199e18, "budget used up");

        // a new window restores the budget
        _warp(24 hours);
        assertEq(exec.soldInWindow(alice, WETH), 0);
        vm.prank(alice);
        exec.reduce(DEMO_ID, 10e6, _dl());
    }

    function test_reduce_discretionary_stops_at_floor() public {
        _setCorridor(alice, DEMO_ID, _corridor(0.5e18, 0.7e18, 0.3e18, 1e18, true, true)); // budget 100%
        _open(alice, DEMO_ID, 1e18, 0.5e18);
        vm.prank(alice);
        exec.reduce(DEMO_ID, 10_000e6, _dl());
        uint256 l = exec.ltvOf(alice, DEMO_ID);
        assertGe(l, 0.3e18, "not below the floor");
        assertLe(l, 0.3005e18, "clamped at the floor");
        // price rises: LTV below the floor, refused
        _warp(COOLDOWN);
        _demoPrice(2600);
        vm.prank(alice);
        vm.expectRevert(Executor.BelowFloor.selector);
        exec.reduce(DEMO_ID, 1e6, _dl());
    }

    function test_reduce_cooldown() public {
        _setCorridor(alice, DEMO_ID, _default());
        _open(alice, DEMO_ID, 1e18, 0.5e18);
        vm.prank(alice);
        exec.reduce(DEMO_ID, 10e6, _dl());
        vm.warp(block.timestamp + COOLDOWN - 1);
        vm.prank(alice);
        vm.expectRevert(Executor.Cooldown.selector);
        exec.reduce(DEMO_ID, 10e6, _dl());
        vm.warp(block.timestamp + 1);
        vm.prank(alice);
        exec.reduce(DEMO_ID, 10e6, _dl());
    }

    function test_reduce_not_refused_on_stale_feed() public {
        _setCorridor(alice, LIVE_ID, _default());
        _open(alice, LIVE_ID, 1e18, 0.5e18);
        _mockFeed(ETH_USD, ethUsdAnswer, block.timestamp - 5000);
        vm.prank(alice);
        exec.reduce(LIVE_ID, 50e6, _dl());
        assertGt(exec.soldInWindow(alice, WETH), 0);
    }

    /// A stale ETH/USD answer 20% low moves the Morpho oracle down with it: LTV reads higher and the sale's bound
    /// (oracle minus 1%) is computed at the stale price. reduce does not check feed age, so the sale goes ahead;
    /// it pays at most the bound at that price, and the live pool, still at the real price, gives better than that.
    function test_reduce_on_stale_low_price_sells_within_bound_at_that_price() public {
        _setCorridor(alice, LIVE_ID, _default());
        _open(alice, LIVE_ID, 1e18, 0.5e18);
        uint256 realPrice = IOracle(LIVE_ORACLE).price();
        _mockFeed(ETH_USD, ethUsdAnswer * 8 / 10, block.timestamp - 5000); // 20% low, 5000 s old (max age 1200 s)
        uint256 stalePrice = IOracle(LIVE_ORACLE).price();
        assertApproxEqRel(stalePrice, realPrice * 8 / 10, 1e9, "Morpho oracle follows the stale feed down 20%");
        uint256 ltvBefore = exec.ltvOf(alice, LIVE_ID);
        assertGt(ltvBefore, 0.5e18, "LTV rose");
        assertLt(ltvBefore, 0.7e18, "still below the act line: discretionary");
        (uint256 c0, uint256 d0) = _pos(alice, LIVE_ID);

        vm.prank(alice);
        exec.reduce(LIVE_ID, 50e6, _dl()); // no StaleFeed
        (uint256 c1, uint256 d1) = _pos(alice, LIVE_ID);
        uint256 paid = c0 - c1;
        uint256 bound = M.maxCollateralIn(50e6, stalePrice);
        assertGt(exec.soldInWindow(alice, WETH), 0, "the sale landed as discretionary");
        assertEq(exec.soldInWindow(alice, WETH), paid, "the sale recorded is the collateral paid");
        assertApproxEqAbs(d0 - d1, 50e6, 1, "debt fell by the amount asked");
        assertLe(paid, bound, "paid within the bound at the stale price");
        assertLt(paid, bound, "the pool at the real price gave more than the stale bound requires");
        _assertExecutorEmpty();
    }

    /// The sale ring holds 16 discretionary sales per caller and collateral token. A 17th inside 24 hours of the
    /// oldest is refused even with the budget nearly untouched; once the oldest leaves the window it goes through.
    function test_reduce_17th_sale_in_24h_refused_until_the_oldest_leaves() public {
        _setCorridor(alice, DEMO_ID, _corridor(0.5e18, 0.7e18, 0.1e18, 0.5e18, true, true));
        _open(alice, DEMO_ID, 1e18, 0.5e18);
        uint256 firstSaleTime = vm.getBlockTimestamp(); // not block.timestamp: via-IR may re-read it at the use
        uint256 firstSale;
        uint256 sum;
        for (uint256 i; i < 16; ++i) {
            if (i > 0) _warp(COOLDOWN);
            (uint256 c0,) = _pos(alice, DEMO_ID);
            vm.prank(alice);
            exec.reduce(DEMO_ID, 1e6, _dl());
            (uint256 c1,) = _pos(alice, DEMO_ID);
            uint256 sale = c0 - c1;
            assertGt(sale, 0, "collateral sold");
            if (i == 0) firstSale = sale;
            sum += sale;
            assertEq(exec.soldInWindow(alice, WETH), sum, "each sale adds to the window");
            assertEq(exec.sessionStart(alice, DEMO_ID), 0, "discretionary: no session");
        }
        (uint256 coll,) = _pos(alice, DEMO_ID);
        assertLt(sum, coll / 100, "under 1% of the collateral sold: far from the 50% budget");

        // the 17th, one cooldown later: every slot is still inside the window
        _warp(COOLDOWN);
        vm.prank(alice);
        vm.expectRevert(Executor.SellBudget.selector);
        exec.reduce(DEMO_ID, 1e6, _dl());

        // one second before the oldest sale is 24 hours old it is still counted
        _warp(firstSaleTime + 24 hours - 1 - vm.getBlockTimestamp());
        assertEq(exec.soldInWindow(alice, WETH), sum, "all 16 still in the window");
        vm.prank(alice);
        vm.expectRevert(Executor.SellBudget.selector);
        exec.reduce(DEMO_ID, 1e6, _dl());

        // at exactly 24 hours the oldest has left the window and its slot takes the 17th sale
        _warp(1);
        assertEq(vm.getBlockTimestamp(), firstSaleTime + 24 hours);
        assertEq(exec.soldInWindow(alice, WETH), sum - firstSale, "the oldest sale left the window");
        (uint256 c2,) = _pos(alice, DEMO_ID);
        vm.prank(alice);
        exec.reduce(DEMO_ID, 1e6, _dl());
        (uint256 c3,) = _pos(alice, DEMO_ID);
        assertEq(exec.soldInWindow(alice, WETH), sum - firstSale + (c2 - c3), "16 sales counted again");
        _assertExecutorEmpty();
    }

    /// Sale ring and sell budget are kept per collateral token: a full WETH ring or a spent WETH budget does not
    /// stop a discretionary sale of TKN by the same caller.
    function test_sale_ring_and_budget_are_per_collateral_token() public {
        _setCorridor(alice, DEMO_ID, _corridor(0.5e18, 0.7e18, 0.1e18, 0.5e18, true, true));
        _setCorridor(alice, ALT_ID, _corridor(0.5e18, 0.7e18, 0.1e18, 0.5e18, true, true));
        _open(alice, DEMO_ID, 1e18, 0.5e18);
        _open(alice, ALT_ID, 10e18, 0.5e18);
        for (uint256 i; i < 16; ++i) {
            if (i > 0) _warp(COOLDOWN);
            vm.prank(alice);
            exec.reduce(DEMO_ID, 1e6, _dl());
        }
        _warp(COOLDOWN);
        vm.prank(alice);
        vm.expectRevert(Executor.SellBudget.selector);
        exec.reduce(DEMO_ID, 1e6, _dl());
        uint256 soldWeth = exec.soldInWindow(alice, WETH);

        // same caller, same moment, other collateral token: its ring is empty
        vm.prank(alice);
        exec.reduce(ALT_ID, 1e6, _dl());
        assertGt(exec.soldInWindow(alice, TKN), 0, "TKN sale recorded");
        assertEq(exec.soldInWindow(alice, WETH), soldWeth, "WETH window unchanged");

        // budgets: after the window, 20% corridors on both; spend the WETH budget, then sell TKN
        _warp(24 hours);
        assertEq(exec.soldInWindow(alice, WETH), 0);
        assertEq(exec.soldInWindow(alice, TKN), 0);
        _setCorridor(alice, DEMO_ID, _corridor(0.5e18, 0.7e18, 0.1e18, 0.2e18, true, true));
        _setCorridor(alice, ALT_ID, _corridor(0.5e18, 0.7e18, 0.1e18, 0.2e18, true, true));
        (uint256 wethColl,) = _pos(alice, DEMO_ID);
        (uint256 tknColl,) = _pos(alice, ALT_ID);
        vm.prank(alice);
        exec.reduce(DEMO_ID, 10_000e6, _dl()); // clamped to the budget
        assertGe(exec.soldInWindow(alice, WETH), wethColl * 0.19e18 / 1e18, "most of the budget in one call");
        // The clamp prices the budget at oracle minus 1%, so a fill near the oracle leaves about 1% of it; each
        // further huge ask sells about 1% of what is left, until the rest is worth less than one loan unit.
        uint256 more;
        while (true) {
            _warp(COOLDOWN);
            vm.prank(alice);
            try exec.reduce(DEMO_ID, 10_000e6, _dl()) {
                ++more;
            } catch (bytes memory err) {
                assertEq(bytes4(err), Executor.SellBudget.selector, "refused for the budget");
                break;
            }
            assertLe(more, 8, "the remainder runs out within a few calls");
        }
        assertGe(exec.soldInWindow(alice, WETH), wethColl * 0.199e18 / 1e18, "WETH budget spent");
        assertLe(exec.soldInWindow(alice, WETH), wethColl * 0.2e18 / 1e18, "within the WETH budget");

        vm.prank(alice);
        exec.reduce(ALT_ID, 10_000e6, _dl()); // clamped to the TKN budget, which is untouched
        uint256 soldTkn = exec.soldInWindow(alice, TKN);
        assertGe(soldTkn, tknColl * 0.19e18 / 1e18, "a full TKN budget's worth sold");
        assertLe(soldTkn, tknColl * 0.2e18 / 1e18, "within the TKN budget");
        _assertExecutorEmpty();
    }

    /// Budget base: only agent-active markets count. Collateral the caller parked on live with no corridor does not
    /// enlarge the budget of the demo position.
    function test_budget_base_ignores_collateral_on_markets_without_a_corridor() public {
        _park(alice, live, 9e18); // no corridor on live
        _setCorridor(alice, DEMO_ID, _corridor(0.5e18, 0.7e18, 0.05e18, 0.2e18, true, true));
        _open(alice, DEMO_ID, 1e18, 0.5e18);
        vm.prank(alice);
        exec.reduce(DEMO_ID, 10_000e6, _dl());
        uint256 sold = exec.soldInWindow(alice, WETH);
        assertLe(sold, 0.2e18, "within 20% of the demo collateral");
        assertGe(sold, 0.19e18, "the budget bound the sale");
        assertGt(exec.ltvOf(alice, DEMO_ID), 0.3e18, "well above the 5% floor");
        _assertExecutorEmpty();
    }

    /// Budget fraction: the smallest sell budget among the agent-active corridors in that token. (i) With collateral
    /// on live (5%) and backup (20%), a sale on backup gets 5%. (ii) After the rest moves to backup, live holds
    /// nothing and is not active, so backup's own 20% applies; the sale already in the window still counts.
    function test_budget_fraction_is_the_smallest_among_active_corridors() public {
        _setCorridor(alice, LIVE_ID, _corridor(0.5e18, 0.7e18, 0.05e18, 0.05e18, true, true));
        _setCorridor(alice, BACKUP_ID, _corridor(0.5e18, 0.7e18, 0.05e18, 0.2e18, true, true));
        _open(alice, LIVE_ID, 1e18, 0.5e18);
        vm.prank(alice);
        exec.migrate(LIVE_ID, BACKUP_ID, 0.5e18, _dl());

        // (i) both markets hold collateral
        (uint256 liveColl,) = _pos(alice, LIVE_ID);
        (uint256 backupColl,) = _pos(alice, BACKUP_ID);
        assertGt(liveColl, 0, "live active");
        assertGt(backupColl, 0, "backup active");
        uint256 left = _budgetLeft(0.05e18, liveColl + backupColl);
        _warp(COOLDOWN);
        vm.prank(alice);
        exec.reduce(BACKUP_ID, 10_000e6, _dl());
        uint256 sold = exec.soldInWindow(alice, WETH);
        assertLe(sold, left, "within live's 5%");
        assertGe(sold, left * 98 / 100, "the 5% bound the sale, not the floor");

        // (ii) everything on backup: live keeps its 5% corridor but holds no collateral
        _warp(COOLDOWN);
        vm.prank(alice);
        exec.migrate(LIVE_ID, BACKUP_ID, 1e18, _dl());
        (liveColl,) = _pos(alice, LIVE_ID);
        (backupColl,) = _pos(alice, BACKUP_ID);
        assertEq(liveColl, 0, "live empty: not active");
        (,,,,,, bool liveSet) = exec.corridors(alice, LIVE_ID);
        assertTrue(liveSet, "live corridor still set");
        left = _budgetLeft(0.2e18, backupColl);
        _warp(COOLDOWN);
        vm.prank(alice);
        exec.reduce(BACKUP_ID, 10_000e6, _dl());
        uint256 sold2 = exec.soldInWindow(alice, WETH);
        assertLe(sold2 - sold, left, "within backup's 20%, the earlier sale counted");
        assertGe(sold2 - sold, left * 98 / 100, "backup's 20% bound the sale");
        assertGt(sold2, 0.05e18, "more than live's 5% in the window: live is not active");
        _assertExecutorEmpty();
    }

    /// Budget base and fraction skip a market closed by exit, even with collateral put back on it through Morpho
    /// (not through open, so it stays closed to the agent): backup's 1% corridor and its collateral do not count.
    function test_budget_base_ignores_a_market_closed_by_exit() public {
        _setCorridor(alice, LIVE_ID, _default()); // budget 20%, floor 30%
        _setCorridor(alice, BACKUP_ID, _corridor(0.5e18, 0.7e18, 0.3e18, 0.01e18, true, true)); // budget 1%
        _open(alice, LIVE_ID, 1e18, 0.5e18);
        _open(alice, BACKUP_ID, 1e18, 0.5e18);
        vm.prank(alice);
        exec.exit(BACKUP_ID, _dl());
        _park(alice, backup, 1e18);
        assertTrue(exec.closedForAgent(alice, BACKUP_ID), "backup closed to the agent");
        (uint256 backupColl,) = _pos(alice, BACKUP_ID);
        assertEq(backupColl, 1e18, "collateral back on backup");

        (uint256 liveColl,) = _pos(alice, LIVE_ID);
        _warp(COOLDOWN);
        vm.prank(alice);
        exec.reduce(LIVE_ID, 10_000e6, _dl());
        uint256 sold = exec.soldInWindow(alice, WETH);
        assertGe(sold, liveColl * 0.19e18 / 1e18, "live's 20% applies: backup's 1% is not active");
        assertLe(sold, liveColl * 0.2e18 / 1e18, "base is live's collateral only");
        _assertExecutorEmpty();
    }

    /// Collateral supplied for `u` straight to Morpho, without the executor: no corridor needed, no open.
    function _park(address u, MarketParams memory mp, uint256 amount) internal {
        vm.startPrank(u);
        IERC20(mp.collateralToken).approve(MORPHO, amount);
        morpho.supplyCollateral(mp, amount, u, "");
        vm.stopPrank();
    }

    /// budget x (active base + sold in window) - sold in window, for alice's WETH.
    function _budgetLeft(uint256 budget, uint256 activeBase) internal view returns (uint256) {
        uint256 s = exec.soldInWindow(alice, WETH);
        uint256 limit = budget * (activeBase + s) / 1e18;
        return limit > s ? limit - s : 0;
    }

    // ------------------------------------------------------------ minimum output

    function test_reduce_refused_when_pool_3pct_below_oracle() public {
        _setCorridor(alice, DEMO_ID, _default());
        _open(alice, DEMO_ID, 1e18, 0.5e18);
        _alignPool(demoPool, WETH, 2400e24, 9_700);
        vm.prank(alice);
        vm.expectRevert(Executor.Slippage.selector);
        exec.reduce(DEMO_ID, 100e6, _dl());
        // 0.5% below: inside the 1% bound, passes
        _alignPool(demoPool, WETH, 2400e24, 9_950);
        vm.prank(alice);
        exec.reduce(DEMO_ID, 100e6, _dl());
        uint256 sold = exec.soldInWindow(alice, WETH);
        assertLe(sold * 2400e24 * 99 / 100, uint256(100e6) * 1e36, "filled no worse than oracle minus 1%");
    }

    function test_exit_refused_when_pool_3pct_below_oracle() public {
        _setCorridor(alice, DEMO_ID, _default());
        _open(alice, DEMO_ID, 1e18, 0.5e18);
        vm.prank(alice);
        IERC20(DUSDC).transfer(carol, 600e6); // half the loan spent: exit must sell
        _alignPool(demoPool, WETH, 2400e24, 9_700);
        vm.prank(alice);
        vm.expectRevert(Executor.Slippage.selector);
        exec.exit(DEMO_ID, _dl());
    }

    // ------------------------------------------------------------ migrate

    function _openLiveWithCorridors() internal {
        _setCorridor(alice, LIVE_ID, _default());
        _setCorridor(alice, BACKUP_ID, _default());
        _open(alice, LIVE_ID, 1e18, 0.5e18);
    }

    function test_migrate_40pct_then_100pct_conserves_collateral_and_debt() public {
        _openLiveWithCorridors();
        (uint256 c0, uint256 d0) = _pos(alice, LIVE_ID);
        uint256 usdcBefore = IERC20(USDC).balanceOf(alice);
        uint256 wethBefore = IERC20(WETH).balanceOf(alice);

        vm.prank(alice);
        exec.migrate(LIVE_ID, BACKUP_ID, 0.4e18, _dl());
        (uint256 cl, uint256 dl) = _pos(alice, LIVE_ID);
        (uint256 cb, uint256 db) = _pos(alice, BACKUP_ID);
        assertEq(cb, 0.4e18, "40% of collateral on backup");
        assertEq(cl + cb, c0, "collateral conserved exactly");
        assertApproxEqAbs(dl + db, d0, 2, "debt conserved within rounding");
        assertApproxEqRel(db * 1e18 / d0, 0.4e18, 0.0001e18);

        _warp(COOLDOWN); // interest accrues on both markets during the cooldown
        morpho.accrueInterest(live);
        morpho.accrueInterest(backup);
        (, dl) = _pos(alice, LIVE_ID);
        (, db) = _pos(alice, BACKUP_ID);
        uint256 d1 = dl + db;
        vm.prank(alice);
        exec.migrate(BACKUP_ID, LIVE_ID, 1e18, _dl());
        (cl, dl) = _pos(alice, LIVE_ID);
        (cb, db) = _pos(alice, BACKUP_ID);
        assertEq(cb, 0);
        assertEq(db, 0);
        assertEq(cl, c0);
        assertApproxEqAbs(dl, d1, 2, "debt conserved by the second move");

        assertEq(IERC20(USDC).balanceOf(alice), usdcBefore, "no tokens moved to or from the caller");
        assertEq(IERC20(WETH).balanceOf(alice), wethBefore);
        _assertExecutorEmpty();
    }

    function test_migrate_third_move_in_24h_refused() public {
        _openLiveWithCorridors();
        vm.prank(alice);
        exec.migrate(LIVE_ID, BACKUP_ID, 1e18, _dl());
        _warp(COOLDOWN);
        vm.prank(alice);
        exec.migrate(BACKUP_ID, LIVE_ID, 1e18, _dl());
        _warp(COOLDOWN);
        vm.prank(alice);
        vm.expectRevert(Executor.MoveLimit.selector);
        exec.migrate(LIVE_ID, BACKUP_ID, 1e18, _dl());
        // 24 h after the first move, one more is allowed
        _warp(24 hours - COOLDOWN * 2);
        vm.prank(alice);
        exec.migrate(LIVE_ID, BACKUP_ID, 1e18, _dl());
    }

    function test_migrate_refused_without_corridor_on_destination() public {
        _setCorridor(alice, LIVE_ID, _default());
        _open(alice, LIVE_ID, 1e18, 0.5e18);
        vm.prank(alice);
        vm.expectRevert(Executor.UnapprovedMarket.selector);
        exec.migrate(LIVE_ID, BACKUP_ID, 1e18, _dl());
    }

    function test_migrate_refused_when_switch_off() public {
        _setCorridor(alice, LIVE_ID, _corridor(0.5e18, 0.7e18, 0.3e18, 0.2e18, false, true));
        _setCorridor(alice, BACKUP_ID, _default());
        _open(alice, LIVE_ID, 1e18, 0.5e18);
        vm.prank(alice);
        vm.expectRevert(Executor.MigrateDisabled.selector);
        exec.migrate(LIVE_ID, BACKUP_ID, 1e18, _dl());
    }

    function test_migrate_refused_when_destination_switch_off() public {
        _setCorridor(alice, LIVE_ID, _default());
        _setCorridor(alice, BACKUP_ID, _corridor(0.5e18, 0.7e18, 0.3e18, 0.2e18, false, true));
        _open(alice, LIVE_ID, 1e18, 0.5e18);
        vm.prank(alice);
        vm.expectRevert(Executor.MigrateDisabled.selector);
        exec.migrate(LIVE_ID, BACKUP_ID, 1e18, _dl());
        // the destination switch was the cause: with it on, the same move succeeds
        _setCorridor(alice, BACKUP_ID, _default());
        vm.prank(alice);
        exec.migrate(LIVE_ID, BACKUP_ID, 1e18, _dl());
        (uint256 cb,) = _pos(alice, BACKUP_ID);
        assertEq(cb, 1e18, "all collateral on backup");
    }

    function test_migrate_refused_between_different_loan_tokens() public {
        _setCorridor(alice, LIVE_ID, _default());
        _setCorridor(alice, DEMO_ID, _default());
        _open(alice, LIVE_ID, 1e18, 0.5e18);
        vm.prank(alice);
        vm.expectRevert(Executor.MarketMismatch.selector);
        exec.migrate(LIVE_ID, DEMO_ID, 1e18, _dl());
    }

    function test_migrate_checks_destination_feed_only() public {
        _openLiveWithCorridors();
        _mockFeed(ETH_USD, ethUsdAnswer, block.timestamp - 1201); // shared by both markets
        vm.prank(alice);
        vm.expectRevert(Executor.StaleFeed.selector);
        exec.migrate(LIVE_ID, BACKUP_ID, 1e18, _dl());
    }

    /// forge-config: default.fuzz.runs = 24
    function testFuzz_migrate_fraction_conserves(uint256 fraction) public {
        fraction = bound(fraction, 1e15, 1e18);
        _openLiveWithCorridors();
        (uint256 c0, uint256 d0) = _pos(alice, LIVE_ID);
        uint256 ltv0 = exec.ltvOf(alice, LIVE_ID);
        vm.prank(alice);
        exec.migrate(LIVE_ID, BACKUP_ID, fraction, _dl());
        (uint256 cl, uint256 dl) = _pos(alice, LIVE_ID);
        (uint256 cb, uint256 db) = _pos(alice, BACKUP_ID);
        assertEq(cl + cb, c0);
        assertTrue(cb == c0 || cb == c0 * fraction / 1e18, "the fraction, or everything when the rest is dust");
        assertApproxEqAbs(dl + db, d0, 2);
        if (cl > 0 && dl > 0) {
            // the part left behind is as healthy as before, up to two units of rounding: Morpho rounds the
            // remaining debt up (at most one unit) and the collateral value is floored (at most one unit)
            uint256 vLeft = cl * IOracle(LIVE_ORACLE).price() / 1e36;
            assertLe(exec.ltvOf(alice, LIVE_ID), ltv0 + 2e18 / vLeft + 1, "source LTV not raised beyond rounding");
        }
        _assertExecutorEmpty();
    }

    function test_migrate_fraction_just_below_100pct_moves_the_dust_too() public {
        _openLiveWithCorridors();
        (uint256 c0,) = _pos(alice, LIVE_ID);
        vm.prank(alice);
        exec.migrate(LIVE_ID, BACKUP_ID, 1e18 - 3, _dl());
        (uint256 cl, uint256 dl) = _pos(alice, LIVE_ID);
        (uint256 cb,) = _pos(alice, BACKUP_ID);
        assertEq(cl, 0);
        assertEq(dl, 0);
        assertEq(cb, c0);
    }

    /// Set ETH/USD to `pct`% of its current answer (fresh, kept by _warp) and move the live pool to the new price.
    function _ethPrice(uint256 pct) internal {
        ethUsdAnswer = ethUsdAnswer * int256(pct) / 100;
        _fresh();
        _alignPool(LIVE_POOL, WETH, IOracle(LIVE_ORACLE).price(), 10_000);
    }

    /// A migrate ends the session on both markets: collateral moved onto a market with an open session never crossed
    /// that market's act line, so it is not sold protectively. A later crossing starts a new session.
    function test_migrate_ends_the_session_on_both_markets() public {
        Executor.Corridor memory c = _corridor(0.4e18, 0.55e18, 0.25e18, 0, true, true);
        _setCorridor(alice, LIVE_ID, c);
        _setCorridor(alice, BACKUP_ID, c);
        _open(alice, BACKUP_ID, 1e18, 0.5e18);
        _open(alice, LIVE_ID, 1e18, 0.45e18);
        _ethPrice(85); // backup 58.8% (above act), live 52.9% (below)
        assertGt(exec.ltvOf(alice, BACKUP_ID), 0.55e18, "backup above the act line");
        assertLt(exec.ltvOf(alice, LIVE_ID), 0.55e18, "live below the act line");

        vm.prank(alice);
        exec.reduce(BACKUP_ID, 10_000e6, _dl());
        assertGt(exec.sessionStart(alice, BACKUP_ID), 0, "session open on backup");
        assertLe(exec.ltvOf(alice, BACKUP_ID), 0.4005e18, "backup sold to target");
        assertEq(exec.soldInWindow(alice, WETH), 0, "protective");

        _warp(COOLDOWN);
        vm.prank(alice);
        exec.migrate(LIVE_ID, BACKUP_ID, 1e18, _dl());
        assertEq(exec.sessionStart(alice, LIVE_ID), 0, "no session on the source");
        assertEq(exec.sessionStart(alice, BACKUP_ID), 0, "migrate ended the session on the destination");
        uint256 l = exec.ltvOf(alice, BACKUP_ID);
        assertGt(l, 0.4e18 + 0.001e18, "merged LTV above target + tolerance");
        assertLt(l, 0.55e18, "merged LTV below the act line");

        _warp(COOLDOWN);
        (uint256 c0,) = _pos(alice, BACKUP_ID);
        vm.prank(alice);
        vm.expectRevert(Executor.SellBudget.selector); // discretionary, budget 0
        exec.reduce(BACKUP_ID, 10_000e6, _dl());
        (uint256 c1,) = _pos(alice, BACKUP_ID);
        assertEq(c1, c0, "no collateral sold");

        // a further drop takes the merged position above the act line: a new session starts
        _ethPrice(80);
        assertGt(exec.ltvOf(alice, BACKUP_ID), 0.55e18, "merged LTV above the act line");
        vm.prank(alice);
        exec.reduce(BACKUP_ID, 10_000e6, _dl());
        (uint256 c2,) = _pos(alice, BACKUP_ID);
        assertLt(c2, c1, "protective sale with budget 0");
        assertGt(exec.sessionStart(alice, BACKUP_ID), 0, "new session open");
        assertEq(exec.soldInWindow(alice, WETH), 0, "protective sales do not use the budget");
        assertGe(exec.ltvOf(alice, BACKUP_ID), 0.4e18, "not below target");
        _assertExecutorEmpty();
    }

    /// A migrate also ends the session on the source: the part left behind starts a new session on the next reduce
    /// if its LTV is still above the act line, with the clock restarted at that call.
    function test_migrate_ends_the_session_on_the_source() public {
        Executor.Corridor memory c = _corridor(0.4e18, 0.55e18, 0.25e18, 0, true, true);
        _setCorridor(alice, LIVE_ID, c);
        _setCorridor(alice, BACKUP_ID, c);
        _open(alice, LIVE_ID, 1e18, 0.5e18);
        _ethPrice(85); // live 58.8%: above the act line
        assertGt(exec.ltvOf(alice, LIVE_ID), 0.55e18, "live above the act line");

        vm.prank(alice);
        exec.reduce(LIVE_ID, 100e6, _dl());
        assertGt(exec.sessionStart(alice, LIVE_ID), 0, "session open on live");

        _warp(COOLDOWN);
        vm.prank(alice);
        exec.migrate(LIVE_ID, BACKUP_ID, 0.5e18, _dl());
        (uint256 cl,) = _pos(alice, LIVE_ID);
        assertGt(cl, 0, "the source keeps a position");
        assertEq(exec.sessionStart(alice, LIVE_ID), 0, "migrate ended the session on the source");
        assertEq(exec.sessionStart(alice, BACKUP_ID), 0, "no session on the destination");
        assertGt(exec.ltvOf(alice, LIVE_ID), 0.55e18, "live still above the act line");

        _warp(COOLDOWN);
        uint256 now_ = vm.getBlockTimestamp(); // not block.timestamp: via-IR may re-read it at the use
        vm.prank(alice);
        exec.reduce(LIVE_ID, 100e6, _dl());
        (uint256 cl2,) = _pos(alice, LIVE_ID);
        assertLt(cl2, cl, "protective sale with budget 0");
        assertEq(exec.sessionStart(alice, LIVE_ID), now_, "new session, clock restarted");
        assertEq(exec.soldInWindow(alice, WETH), 0, "protective sales do not use the budget");
        _assertExecutorEmpty();
    }

    // ------------------------------------------------------------ exit

    function test_exit_from_idle_usdc_withdraws_everything() public {
        _openLiveWithCorridors();
        uint256 wethBefore = IERC20(WETH).balanceOf(alice);
        vm.prank(alice);
        exec.exit(LIVE_ID, _dl());
        (uint256 c, uint256 d) = _pos(alice, LIVE_ID);
        assertEq(c, 0);
        assertEq(d, 0);
        (, uint128 shares,) = morpho.position(LIVE_ID, alice);
        assertEq(shares, 0, "repaid by shares: no dust debt");
        assertEq(IERC20(WETH).balanceOf(alice), wethBefore + 1e18, "all collateral, nothing sold");
        assertTrue(exec.closedForAgent(alice, LIVE_ID));
        _warp(COOLDOWN);
        vm.prank(alice);
        vm.expectRevert(Executor.UnapprovedMarket.selector);
        exec.reduce(LIVE_ID, 1e6, _dl());
        // the owner re-opens
        _open(alice, LIVE_ID, 1e18, 0.4e18);
        assertFalse(exec.closedForAgent(alice, LIVE_ID));
        _assertExecutorEmpty();
    }

    function test_exit_sells_collateral_for_the_rest() public {
        _setCorridor(alice, DEMO_ID, _default());
        _open(alice, DEMO_ID, 1e18, 0.5e18);
        vm.prank(alice);
        IERC20(DUSDC).transfer(carol, 600e6);
        uint256 wethBefore = IERC20(WETH).balanceOf(alice);
        vm.prank(alice);
        exec.exit(DEMO_ID, _dl());
        (uint256 c, uint256 d) = _pos(alice, DEMO_ID);
        assertEq(c, 0);
        assertEq(d, 0);
        assertEq(IERC20(DUSDC).balanceOf(alice), 0, "idle loan token used first");
        uint256 back = IERC20(WETH).balanceOf(alice) - wethBefore;
        assertApproxEqRel(1e18 - back, 0.25e18, 0.002e18, "about 600/2400 WETH sold");
        _assertExecutorEmpty();
    }

    function test_exit_refused_when_switch_off() public {
        _setCorridor(alice, DEMO_ID, _corridor(0.5e18, 0.7e18, 0.3e18, 0.2e18, true, false));
        _open(alice, DEMO_ID, 1e18, 0.5e18);
        vm.prank(alice);
        vm.expectRevert(Executor.ExitDisabled.selector);
        exec.exit(DEMO_ID, _dl());
    }

    function test_exit_not_refused_on_stale_feed() public {
        _openLiveWithCorridors();
        _mockFeed(ETH_USD, ethUsdAnswer, block.timestamp - 5000);
        vm.prank(alice);
        exec.exit(LIVE_ID, _dl());
    }

    // ------------------------------------------------------------ owner paths

    function test_repay_partial_and_full() public {
        _open(alice, DEMO_ID, 1e18, 0.5e18);
        (, uint256 d0) = _pos(alice, DEMO_ID);
        vm.prank(alice);
        exec.repay(DEMO_ID, 200e6, _dl());
        (, uint256 d1) = _pos(alice, DEMO_ID);
        assertApproxEqAbs(d0 - d1, 200e6, 1);
        _fund(DUSDC, alice, 10e6);
        vm.prank(alice);
        exec.repay(DEMO_ID, type(uint256).max, _dl());
        (, uint128 shares,) = morpho.position(DEMO_ID, alice);
        assertEq(shares, 0);
        _assertExecutorEmpty();
    }

    function test_close_returns_every_balance() public {
        _setCorridor(alice, DEMO_ID, _default());
        _open(alice, DEMO_ID, 1e18, 0.5e18);
        _warp(30 days); // interest accrues: the borrowed amount no longer covers the debt
        _fund(DUSDC, alice, 50e6);
        uint256 dusdcBefore = IERC20(DUSDC).balanceOf(alice);
        (, uint256 debt) = _pos(alice, DEMO_ID);
        vm.prank(alice);
        exec.close(DEMO_ID, _dl());
        (uint256 c, uint256 d) = _pos(alice, DEMO_ID);
        (, uint128 shares,) = morpho.position(DEMO_ID, alice);
        assertEq(c, 0);
        assertEq(d, 0);
        assertEq(shares, 0);
        assertEq(IERC20(WETH).balanceOf(alice), 10e18, "all collateral back");
        assertGe(dusdcBefore - IERC20(DUSDC).balanceOf(alice), debt - 1, "exact remaining debt pulled");
        _assertExecutorEmpty();
    }

    // ------------------------------------------------------------ recipient pin

    function test_recipient_pin_caller_cannot_touch_another_position() public {
        _setCorridor(alice, DEMO_ID, _default());
        _open(alice, DEMO_ID, 1e18, 0.5e18);
        (uint256 ca, uint256 da) = _pos(alice, DEMO_ID);
        uint256[3] memory aliceBal = [IERC20(WETH).balanceOf(alice), IERC20(DUSDC).balanceOf(alice), IERC20(USDC).balanceOf(alice)];
        uint256[3] memory carolBal = [IERC20(WETH).balanceOf(carol), IERC20(DUSDC).balanceOf(carol), IERC20(USDC).balanceOf(carol)];

        // bob has no position and no corridor: every agent action is refused, owner actions act on bob's empty position
        vm.startPrank(bob);
        vm.expectRevert(Executor.UnapprovedMarket.selector);
        exec.reduce(DEMO_ID, 100e6, _dl());
        vm.expectRevert(Executor.UnapprovedMarket.selector);
        exec.exit(DEMO_ID, _dl());
        vm.expectRevert(Executor.UnapprovedMarket.selector);
        exec.migrate(DEMO_ID, LIVE_ID, 1e18, _dl());
        exec.close(DEMO_ID, _dl()); // closes bob's (empty) position
        vm.expectRevert(Executor.NoDebt.selector);
        exec.repay(DEMO_ID, 1e6, _dl());
        // with his own corridor bob still acts only on his own (empty) position
        exec.setCorridor(DEMO_ID, _default(), _dl());
        vm.expectRevert(Executor.NoDebt.selector);
        exec.reduce(DEMO_ID, 100e6, _dl());
        exec.exit(DEMO_ID, _dl()); // nothing to repay, nothing to withdraw
        vm.stopPrank();

        // callbacks cannot be driven from outside
        vm.startPrank(bob);
        vm.expectRevert(Executor.NotMorpho.selector);
        exec.onMorphoRepay(100e6, abi.encode(Executor.Op.SellForRepay, alice, DEMO_ID, uint256(2400e24)));
        vm.expectRevert(Executor.NotMorpho.selector);
        exec.onMorphoFlashLoan(100e6, abi.encode(Executor.Op.Migrate, alice, DEMO_ID, LIVE_ID, uint256(1), uint256(1)));
        vm.expectRevert(Executor.NotPool.selector);
        exec.uniswapV3SwapCallback(1e18, 0, abi.encode(true, alice, DEMO_ID, address(0), type(uint256).max));
        vm.stopPrank();
        // Morpho itself calling back outside an executor operation is refused too
        vm.prank(MORPHO);
        vm.expectRevert(Executor.NotMorpho.selector);
        exec.onMorphoRepay(100e6, abi.encode(Executor.Op.SellForRepay, alice, DEMO_ID, uint256(2400e24)));
        // the demo pool calling the swap callback outside a swap is refused
        vm.prank(demoPool);
        vm.expectRevert(Executor.NotPool.selector);
        exec.uniswapV3SwapCallback(1e18, 0, abi.encode(true, alice, DEMO_ID, address(0), type(uint256).max));

        (uint256 ca2, uint256 da2) = _pos(alice, DEMO_ID);
        assertEq(ca2, ca, "alice collateral untouched");
        assertEq(da2, da, "alice debt untouched");
        assertEq(IERC20(WETH).balanceOf(alice), aliceBal[0]);
        assertEq(IERC20(DUSDC).balanceOf(alice), aliceBal[1]);
        assertEq(IERC20(USDC).balanceOf(alice), aliceBal[2]);
        assertEq(IERC20(WETH).balanceOf(carol), carolBal[0], "carol receives nothing");
        assertEq(IERC20(DUSDC).balanceOf(carol), carolBal[1]);
        assertEq(IERC20(USDC).balanceOf(carol), carolBal[2]);
    }

    function test_recipient_pin_every_output_goes_to_the_caller() public {
        // alice's own actions send outputs only to alice, never to anyone else
        _setCorridor(alice, DEMO_ID, _default());
        uint256 carolW = IERC20(WETH).balanceOf(carol);
        uint256 carolD = IERC20(DUSDC).balanceOf(carol);
        _open(alice, DEMO_ID, 1e18, 0.5e18);
        vm.prank(alice);
        exec.reduce(DEMO_ID, 100e6, _dl());
        _warp(COOLDOWN);
        vm.prank(alice);
        exec.exit(DEMO_ID, _dl());
        assertEq(IERC20(WETH).balanceOf(carol), carolW);
        assertEq(IERC20(DUSDC).balanceOf(carol), carolD);
        _assertExecutorEmpty();
    }

    function test_donation_does_not_block_calls() public {
        _fund(WETH, address(exec), 1);
        _fund(DUSDC, address(exec), 1);
        _setCorridor(alice, DEMO_ID, _default());
        _open(alice, DEMO_ID, 1e18, 0.5e18);
        vm.prank(alice);
        exec.reduce(DEMO_ID, 100e6, _dl());
    }

    // ------------------------------------------------------------ deadlines

    function test_expired_deadline_reverts_everywhere() public {
        _setCorridor(alice, DEMO_ID, _default());
        _setCorridor(alice, LIVE_ID, _default());
        _setCorridor(alice, BACKUP_ID, _default());
        _open(alice, DEMO_ID, 1e18, 0.5e18);
        uint256 past = block.timestamp - 1;
        vm.startPrank(alice);
        vm.expectRevert(Executor.Expired.selector);
        exec.reduce(DEMO_ID, 1e6, past);
        vm.expectRevert(Executor.Expired.selector);
        exec.exit(DEMO_ID, past);
        vm.expectRevert(Executor.Expired.selector);
        exec.migrate(LIVE_ID, BACKUP_ID, 1e18, past);
        vm.expectRevert(Executor.Expired.selector);
        exec.open(DEMO_ID, 1e18, 0.5e18, past);
        vm.expectRevert(Executor.Expired.selector);
        exec.openFromStables(DEMO_ID, 1e6, 0.5e18, past);
        vm.expectRevert(Executor.Expired.selector);
        exec.repay(DEMO_ID, 1e6, past);
        vm.expectRevert(Executor.Expired.selector);
        exec.close(DEMO_ID, past);
        vm.expectRevert(Executor.Expired.selector);
        exec.setCorridor(DEMO_ID, _default(), past);
        // the deadline second itself is still valid
        exec.reduce(DEMO_ID, 1e6, block.timestamp);
        vm.stopPrank();
    }

    // ------------------------------------------------------------ corridor validation

    function testFuzz_setCorridor_ordering(uint64 floor, uint64 target, uint64 act, uint64 budget) public {
        floor = uint64(bound(floor, 0, 1e18));
        target = uint64(bound(target, 0, 1e18));
        act = uint64(bound(act, 0, 1e18));
        budget = uint64(bound(budget, 0, 1.2e18));
        bool ok = floor < target && target + 0.001e18 < act && act < LLTV && budget <= 1e18;
        vm.prank(alice);
        if (!ok) vm.expectRevert(Executor.BadCorridor.selector);
        exec.setCorridor(DEMO_ID, _corridor(target, act, floor, budget, true, true), _dl());
        if (ok) {
            (uint64 t, uint64 a, uint64 f, uint64 b,,, bool set) = exec.corridors(alice, DEMO_ID);
            assertEq(t, target);
            assertEq(a, act);
            assertEq(f, floor);
            assertEq(b, budget);
            assertTrue(set);
        }
    }

    function test_setCorridor_act_within_tolerance_of_target_refused() public {
        vm.prank(alice);
        vm.expectRevert(Executor.BadCorridor.selector);
        exec.setCorridor(DEMO_ID, _corridor(0.5e18, 0.5e18 + 0.001e18, 0.3e18, 0.2e18, true, true), _dl());
        _setCorridor(alice, DEMO_ID, _corridor(0.5e18, 0.5e18 + 0.001e18 + 1, 0.3e18, 0.2e18, true, true));
        (, uint64 a,,,,, bool set) = exec.corridors(alice, DEMO_ID);
        assertEq(a, 0.5e18 + 0.001e18 + 1);
        assertTrue(set);
    }

    function test_setCorridor_unknown_market() public {
        vm.prank(alice);
        vm.expectRevert(Executor.UnknownMarket.selector);
        exec.setCorridor(keccak256("nope"), _default(), _dl());
    }

    // ------------------------------------------------------------ budget arithmetic on the fork

    /// forge-config: default.fuzz.runs = 24
    function testFuzz_budget_never_exceeded(uint96[6] memory amounts, uint64 budget) public {
        budget = uint64(bound(budget, 0.01e18, 0.5e18));
        _setCorridor(alice, DEMO_ID, _corridor(0.5e18, 0.7e18, 0.05e18, budget, true, true));
        _open(alice, DEMO_ID, 1e18, 0.5e18);
        uint256 limit = uint256(1e18) * budget / 1e18; // collateral at the start of the window
        for (uint256 i; i < amounts.length; ++i) {
            uint256 a = bound(amounts[i], 1e6, 2_000e6);
            vm.prank(alice);
            try exec.reduce(DEMO_ID, a, _dl()) {} catch {}
            assertLe(exec.soldInWindow(alice, WETH), limit, "sold within the budget");
            assertGe(exec.ltvOf(alice, DEMO_ID), 0.05e18, "not below the floor");
            _warp(COOLDOWN);
        }
    }
}
