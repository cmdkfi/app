// SPDX-License-Identifier: MIT
pragma solidity 0.8.28;

/// Pure arithmetic of the corridor, kept apart so it can be fuzzed without a fork.
/// Units: LTV values are WAD (1e18 = 100%); debt and collateral value are in loan-token units;
/// prices are Morpho oracle prices (loan units per collateral unit, scaled by 1e36).
library CorridorMath {
    uint256 internal constant WAD = 1e18;
    uint256 internal constant ORACLE_SCALE = 1e36;
    uint256 internal constant BPS = 10_000;
    uint256 internal constant SLIPPAGE_BPS = 100; // 1%: fills no worse than 1% below Morpho's oracle
    uint256 internal constant VIRTUAL_SHARES = 1e6; // Morpho SharesMathLib
    uint256 internal constant VIRTUAL_ASSETS = 1;
    /// 0.1 percentage points: a protective session ends once LTV is this close to target, so act must sit above it.
    uint256 internal constant TARGET_TOLERANCE = 0.001e18;

    /// Morpho SharesMathLib.toAssetsUp: debt of a borrower.
    function toAssetsUp(uint256 shares, uint256 totalAssets, uint256 totalShares) internal pure returns (uint256) {
        uint256 num = shares * (totalAssets + VIRTUAL_ASSETS);
        uint256 den = totalShares + VIRTUAL_SHARES;
        return (num + den - 1) / den;
    }

    /// Collateral value in loan units at the oracle price, rounded down (so LTV rounds up).
    function value(uint256 collateral, uint256 price) internal pure returns (uint256) {
        return collateral * price / ORACLE_SCALE;
    }

    /// LTV in WAD; max uint if the collateral is worth nothing and there is debt.
    function ltv(uint256 debt, uint256 collValue) internal pure returns (uint256) {
        if (debt == 0) return 0;
        if (collValue == 0) return type(uint256).max;
        return (debt * WAD + collValue - 1) / collValue; // rounded up
    }

    /// Largest repay amount x such that selling collateral worth x at the oracle price leaves LTV >= t:
    /// (D - x) / (V - x) >= t  <=>  x <= (D - tV) / (1 - t). Zero if LTV is already at or below t.
    /// Capped at the debt. A fill below the oracle sells more collateral, which only raises the LTV after.
    function repayToLtv(uint256 debt, uint256 collValue, uint256 t) internal pure returns (uint256) {
        if (t >= WAD) return 0;
        uint256 dw = debt * WAD;
        uint256 tv = t * collValue;
        if (dw <= tv) return 0;
        uint256 x = (dw - tv) / (WAD - t);
        return x > debt ? debt : x;
    }

    /// Most collateral that may be paid for `out` loan units: price no worse than oracle minus 1%.
    /// owed * price * (1 - 1%) <= out * 1e36
    function maxCollateralIn(uint256 out, uint256 price) internal pure returns (uint256) {
        return out * ORACLE_SCALE * BPS / (price * (BPS - SLIPPAGE_BPS));
    }

    /// Least collateral that must be received for `amountIn` loan units: oracle price minus 1%.
    function minCollateralOut(uint256 amountIn, uint256 price) internal pure returns (uint256) {
        return amountIn * ORACLE_SCALE * (BPS - SLIPPAGE_BPS) / (price * BPS);
    }

    /// Loan units that `collateral` is guaranteed to fetch at the worst allowed fill (oracle minus 1%).
    function worstCaseOut(uint256 collateral, uint256 price) internal pure returns (uint256) {
        return collateral * price * (BPS - SLIPPAGE_BPS) / (ORACLE_SCALE * BPS);
    }

    /// Remaining discretionary sell budget in collateral units: budget fraction of the caller's collateral
    /// at the start of the window (current collateral plus what was sold in the window), minus what was sold.
    function remainingBudget(uint256 totalCollateral, uint256 soldInWindow, uint256 budgetWad)
        internal
        pure
        returns (uint256)
    {
        uint256 limit = (totalCollateral + soldInWindow) * budgetWad / WAD;
        return limit > soldInWindow ? limit - soldInWindow : 0;
    }

    /// floor < target, target + TARGET_TOLERANCE < act < lltv, sell budget at most 100%.
    function corridorValid(uint256 floor, uint256 target, uint256 act, uint256 lltv, uint256 sellBudget)
        internal
        pure
        returns (bool)
    {
        return floor < target && target + TARGET_TOLERANCE < act && act < lltv && sellBudget <= WAD;
    }
}
