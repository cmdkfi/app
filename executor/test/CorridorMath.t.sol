// SPDX-License-Identifier: MIT
pragma solidity 0.8.28;

import {Test} from "forge-std/Test.sol";
import {CorridorMath as M} from "../src/CorridorMath.sol";

/// Pure fuzzing of the corridor arithmetic (no fork).
contract CorridorMathTest is Test {
    uint256 constant WAD = 1e18;

    /// Selling collateral worth x at the oracle price leaves LTV >= t, and x is the largest such amount.
    function testFuzz_repayToLtv_stops_at_line(uint256 debt, uint256 value, uint256 t) public pure {
        debt = bound(debt, 1, 1e15); // up to 1e9 USDC
        value = bound(value, 1, 1e16);
        t = bound(t, 0, 0.86e18);
        uint256 x = M.repayToLtv(debt, value, t);
        assertLe(x, debt);
        if (x == 0) return;
        // LTV after (D - x) / (V - x) >= t, i.e. (D - x) * WAD >= t * (V - x) (V - x may go negative: then LTV is infinite)
        if (value > x) assertGe((debt - x) * WAD, t * (value - x), "LTV after at or above the line");
        // maximal: one more unit would cross the line (unless capped by the debt)
        if (x < debt && value > x + 1) assertLt((debt - x - 1) * WAD, t * (value - x - 1), "x is the largest amount");
    }

    function testFuzz_repayToLtv_zero_at_or_below_line(uint256 debt, uint256 value, uint256 t) public pure {
        value = bound(value, 1, 1e16);
        t = bound(t, 1, 0.86e18);
        debt = bound(debt, 0, value * t / WAD);
        assertEq(M.repayToLtv(debt, value, t), 0);
    }

    /// Paying at most maxCollateralIn for `out` means a fill no worse than oracle minus 1%.
    function testFuzz_maxCollateralIn_bound(uint256 out, uint256 price) public pure {
        out = bound(out, 1, 1e15);
        price = bound(price, 1e22, 1e30); // $0.01 .. $1,000,000 per ETH at the USDC/WETH scale
        uint256 maxIn = M.maxCollateralIn(out, price);
        assertLe(maxIn * price * 99, out * 1e36 * 100, "maxIn at the oracle minus 1% is worth at most out");
        assertGt((maxIn + 1) * price * 99, out * 1e36 * 100, "maxIn is tight");
    }

    /// worstCaseOut(c) loan units can always be bought with c collateral inside the 1% bound.
    function testFuzz_worstCaseOut_within_budget(uint256 coll, uint256 price) public pure {
        coll = bound(coll, 0, 1e24);
        price = bound(price, 1e22, 1e30);
        uint256 out = M.worstCaseOut(coll, price);
        assertLe(M.maxCollateralIn(out, price), coll, "collateral paid for the budget amount stays in the budget");
    }

    /// Sold plus remaining never exceeds the budget fraction of collateral at the window start.
    function testFuzz_remainingBudget(uint256 total, uint256 sold, uint256 budget) public pure {
        total = bound(total, 0, 1e30);
        sold = bound(sold, 0, 1e30);
        budget = bound(budget, 0, WAD);
        uint256 r = M.remainingBudget(total, sold, budget);
        uint256 limit = (total + sold) * budget / WAD;
        assertLe(sold + r, limit > sold ? limit : sold);
        if (sold >= limit) assertEq(r, 0);
    }

    function testFuzz_corridorValid(uint256 f, uint256 t, uint256 a, uint256 b) public pure {
        f = bound(f, 0, 1.5e18);
        t = bound(t, 0, 1.5e18);
        a = bound(a, 0, 1.5e18);
        b = bound(b, 0, 1.5e18);
        assertEq(M.corridorValid(f, t, a, 0.86e18, b), f < t && t + 0.001e18 < a && a < 0.86e18 && b <= WAD);
    }

    /// act within TARGET_TOLERANCE of target is refused; one unit above it is accepted.
    function test_corridorValid_act_tolerance_boundary() public pure {
        assertFalse(M.corridorValid(0.3e18, 0.5e18, 0.5e18 + 0.001e18, 0.86e18, 0.2e18));
        assertTrue(M.corridorValid(0.3e18, 0.5e18, 0.5e18 + 0.001e18 + 1, 0.86e18, 0.2e18));
    }

    /// toAssetsUp matches Morpho's SharesMathLib (mulDivUp with 1e6 virtual shares, 1 virtual asset).
    function testFuzz_toAssetsUp(uint128 shares, uint128 ta, uint128 ts) public pure {
        uint256 r = M.toAssetsUp(shares, ta, ts);
        uint256 num = uint256(shares) * (uint256(ta) + 1);
        uint256 den = uint256(ts) + 1e6;
        assertEq(r, num / den + (num % den == 0 ? 0 : 1));
    }

    function test_ltv_values() public pure {
        assertEq(M.ltv(0, 0), 0);
        assertEq(M.ltv(1, 0), type(uint256).max);
        assertEq(M.ltv(50, 100), 0.5e18);
        assertEq(M.ltv(1, 3), 333333333333333334); // rounded up
    }
}
