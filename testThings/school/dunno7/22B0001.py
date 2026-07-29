import sys


def solve():
    line = sys.stdin.read().split()
    if not line:
        return

    M = float(line[0])
    S = float(line[1])
    N = int(float(line[2]))

    n_months = 12 * N

    # 1. 二分搜尋法尋找 Y (年化報酬率)
    low = 0.0
    high = 1.0  # 題目說明 Y <= 100% (即 1.0)

    for _ in range(100):  # 疊代 100 次達到高精度
        mid = (low + high) / 2.0

        # 依題目提示：月化報酬率 = 年化報酬率 / 12
        r = mid / 12.0

        # 用迴圈模擬每個月初投入 M 元，避免 (1+r)^12000 造成溢位 (Overflow)
        current_S = 0.0
        for _ in range(n_months):
            current_S = (current_S + M) * (1 + r)
            if current_S > S:  # 提前剪枝，防止數字過大
                break

        if current_S < S:
            low = mid
        else:
            high = mid

    Y = low  # 找到的年化報酬率 (小數形式)

    # 2. 計算單筆投入本金 P
    # P * (1 + Y)^N = S  =>  P = S / ((1 + Y) ** N)
    P = S / ((1 + Y) ** N)

    # 3. 本金差距 = 定期定額總本金 - 單筆投入本金
    total_M_money = M * n_months
    diff = abs(total_M_money - P)

    # 精準四捨五入到小數點下兩位的函式
    def round2(val):
        return f"{int(val * 100 + 0.5 + 1e-9) / 100:.2f}"

    # 4. 輸出結果 (Y 需乘以 100 轉成百分比)
    y_percent = f"{int(Y * 10000 + 0.5 + 1e-9) / 100:.2f}%"
    print(f"{y_percent} {round2(P)} {round2(diff)}")


if __name__ == "__main__":
    solve()