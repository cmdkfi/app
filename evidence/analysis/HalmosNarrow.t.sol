// SPDX-License-Identifier: MIT
pragma solidity 0.8.28;

import {Test} from "forge-std/Test.sol";
import {CorridorMath as M} from "../src/CorridorMath.sol";

/// The six nonlinear CorridorMath properties on bounded domains (64-bit amounts, prices up to 2^80),
/// so the SMT solver works on narrower bit-vectors. Same claims as Halmos.t.sol.
contract CorridorMathHalmosNarrow is Test {
    uint256 constant WAD = 1e18;

    function check_repayToLtv_stops_at_line(uint64 debt_, uint64 value_, uint64 t_) public pure {
        uint256 debt = debt_; uint256 value = value_; uint256 t = t_;
        vm.assume(debt >= 1 && value >= 1 && t <= 0.86e18);
        uint256 x = M.repayToLtv(debt, value, t);
        assert(x <= debt);
        if (x == 0) return;
        if (value > x) assert((debt - x) * WAD >= t * (value - x));
        if (x < debt && value > x + 1) assert((debt - x - 1) * WAD < t * (value - x - 1));
    }

    function check_repayToLtv_zero_at_or_below_line(uint64 debt_, uint64 value_, uint64 t_) public pure {
        uint256 debt = debt_; uint256 value = value_; uint256 t = t_;
        vm.assume(value >= 1 && t >= 1 && t <= 0.86e18);
        vm.assume(debt <= value * t / WAD);
        assert(M.repayToLtv(debt, value, t) == 0);
    }

    function check_maxCollateralIn_bound(uint64 out_, uint80 price_) public pure {
        uint256 out = out_; uint256 price = price_;
        vm.assume(out >= 1 && price >= 1e22);
        uint256 maxIn = M.maxCollateralIn(out, price);
        assert(maxIn * price * 99 <= out * 1e36 * 100);
        assert((maxIn + 1) * price * 99 > out * 1e36 * 100);
    }

    function check_worstCaseOut_within_budget(uint64 coll_, uint80 price_) public pure {
        uint256 coll = coll_; uint256 price = price_;
        vm.assume(price >= 1e22);
        uint256 out = M.worstCaseOut(coll, price);
        assert(M.maxCollateralIn(out, price) <= coll);
    }

    function check_toAssetsUp(uint64 shares, uint64 ta, uint64 ts) public pure {
        uint256 r = M.toAssetsUp(shares, ta, ts);
        uint256 num = uint256(shares) * (uint256(ta) + 1);
        uint256 den = uint256(ts) + 1e6;
        assert(r == num / den + (num % den == 0 ? 0 : 1));
    }

    function check_ltv_rounds_up_and_bounds(uint64 debt_, uint64 v_) public pure {
        uint256 debt = debt_; uint256 v = v_;
        vm.assume(v >= 1);
        uint256 l = M.ltv(debt, v);
        assert(l * v >= debt * WAD);
        if (l > 0) assert((l - 1) * v < debt * WAD);
    }
}
