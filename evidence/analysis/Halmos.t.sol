// SPDX-License-Identifier: MIT
pragma solidity 0.8.28;

import {Test} from "forge-std/Test.sol";
import {CorridorMath as M} from "../src/CorridorMath.sol";

/// Symbolic (Halmos) versions of the CorridorMath fuzz properties: same claims, every input in the stated ranges.
contract CorridorMathHalmos is Test {
    uint256 constant WAD = 1e18;

    function check_repayToLtv_stops_at_line(uint256 debt, uint256 value, uint256 t) public pure {
        vm.assume(debt >= 1 && debt <= 1e15);
        vm.assume(value >= 1 && value <= 1e16);
        vm.assume(t <= 0.86e18);
        uint256 x = M.repayToLtv(debt, value, t);
        assert(x <= debt);
        if (x == 0) return;
        if (value > x) assert((debt - x) * WAD >= t * (value - x));
        if (x < debt && value > x + 1) assert((debt - x - 1) * WAD < t * (value - x - 1));
    }

    function check_repayToLtv_zero_at_or_below_line(uint256 debt, uint256 value, uint256 t) public pure {
        vm.assume(value >= 1 && value <= 1e16);
        vm.assume(t >= 1 && t <= 0.86e18);
        vm.assume(debt <= value * t / WAD);
        assert(M.repayToLtv(debt, value, t) == 0);
    }

    function check_maxCollateralIn_bound(uint256 out, uint256 price) public pure {
        vm.assume(out >= 1 && out <= 1e15);
        vm.assume(price >= 1e22 && price <= 1e30);
        uint256 maxIn = M.maxCollateralIn(out, price);
        assert(maxIn * price * 99 <= out * 1e36 * 100);
        assert((maxIn + 1) * price * 99 > out * 1e36 * 100);
    }

    function check_worstCaseOut_within_budget(uint256 coll, uint256 price) public pure {
        vm.assume(coll <= 1e24);
        vm.assume(price >= 1e22 && price <= 1e30);
        uint256 out = M.worstCaseOut(coll, price);
        assert(M.maxCollateralIn(out, price) <= coll);
    }

    function check_remainingBudget(uint256 total, uint256 sold, uint256 budget) public pure {
        vm.assume(total <= 1e30);
        vm.assume(sold <= 1e30);
        vm.assume(budget <= WAD);
        uint256 r = M.remainingBudget(total, sold, budget);
        uint256 limit = (total + sold) * budget / WAD;
        assert(sold + r <= (limit > sold ? limit : sold));
        if (sold >= limit) assert(r == 0);
    }

    function check_corridorValid(uint256 f, uint256 t, uint256 a, uint256 b) public pure {
        vm.assume(f <= 1.5e18 && t <= 1.5e18 && a <= 1.5e18 && b <= 1.5e18);
        assert(M.corridorValid(f, t, a, 0.86e18, b) == (f < t && t + 0.001e18 < a && a < 0.86e18 && b <= WAD));
    }

    function check_toAssetsUp(uint128 shares, uint128 ta, uint128 ts) public pure {
        uint256 r = M.toAssetsUp(shares, ta, ts);
        uint256 num = uint256(shares) * (uint256(ta) + 1);
        uint256 den = uint256(ts) + 1e6;
        assert(r == num / den + (num % den == 0 ? 0 : 1));
    }

    function check_ltv_rounds_up_and_bounds(uint256 debt, uint256 v) public pure {
        vm.assume(v >= 1 && v <= 1e30 && debt <= 1e30);
        uint256 l = M.ltv(debt, v);
        // l is the smallest WAD value with l * v >= debt * WAD
        assert(l * v >= debt * WAD);
        if (l > 0) assert((l - 1) * v < debt * WAD);
    }
}
