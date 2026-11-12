// SPDX-License-Identifier: MIT
pragma solidity 0.8.28;

import {CorridorMath as M} from "../src/CorridorMath.sol";

/// Medusa assertion-mode harness over CorridorMath: the same properties as CorridorMath.t.sol, inputs clamped by modulo.
contract CorridorMathMedusa {
    uint256 constant WAD = 1e18;

    function repayToLtv_stops_at_line(uint256 debt, uint256 value, uint256 t) public pure {
        debt = 1 + debt % 1e15; value = 1 + value % 1e16; t = t % (0.86e18 + 1);
        uint256 x = M.repayToLtv(debt, value, t);
        assert(x <= debt);
        if (x == 0) return;
        if (value > x) assert((debt - x) * WAD >= t * (value - x));
        if (x < debt && value > x + 1) assert((debt - x - 1) * WAD < t * (value - x - 1));
    }

    function repayToLtv_zero_at_or_below_line(uint256 debt, uint256 value, uint256 t) public pure {
        value = 1 + value % 1e16; t = 1 + t % 0.86e18; debt = debt % (value * t / WAD + 1);
        assert(M.repayToLtv(debt, value, t) == 0);
    }

    function maxCollateralIn_bound(uint256 out, uint256 price) public pure {
        out = 1 + out % 1e15; price = 1e22 + price % (1e30 - 1e22 + 1);
        uint256 maxIn = M.maxCollateralIn(out, price);
        assert(maxIn * price * 99 <= out * 1e36 * 100);
        assert((maxIn + 1) * price * 99 > out * 1e36 * 100);
    }

    function worstCaseOut_within_budget(uint256 coll, uint256 price) public pure {
        coll = coll % (1e24 + 1); price = 1e22 + price % (1e30 - 1e22 + 1);
        uint256 out = M.worstCaseOut(coll, price);
        assert(M.maxCollateralIn(out, price) <= coll);
    }

    function remainingBudget(uint256 total, uint256 sold, uint256 budget) public pure {
        total = total % (1e30 + 1); sold = sold % (1e30 + 1); budget = budget % (WAD + 1);
        uint256 r = M.remainingBudget(total, sold, budget);
        uint256 limit = (total + sold) * budget / WAD;
        assert(sold + r <= (limit > sold ? limit : sold));
        if (sold >= limit) assert(r == 0);
    }

    function corridorValid(uint256 f, uint256 t, uint256 a, uint256 b) public pure {
        f = f % (1.5e18 + 1); t = t % (1.5e18 + 1); a = a % (1.5e18 + 1); b = b % (1.5e18 + 1);
        assert(M.corridorValid(f, t, a, 0.86e18, b) == (f < t && t + 0.001e18 < a && a < 0.86e18 && b <= WAD));
    }

    function toAssetsUp(uint128 shares, uint128 ta, uint128 ts) public pure {
        uint256 r = M.toAssetsUp(shares, ta, ts);
        uint256 num = uint256(shares) * (uint256(ta) + 1);
        uint256 den = uint256(ts) + 1e6;
        assert(r == num / den + (num % den == 0 ? 0 : 1));
    }
}
