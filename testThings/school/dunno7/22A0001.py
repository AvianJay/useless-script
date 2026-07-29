import sys


def main():
    data = sys.stdin.buffer.read().split()
    if not data:
        sys.stdout.write("[]\n")
        return

    n = int(data[0])
    if n <= 0:
        sys.stdout.write("[]\n")
        return

    nums = list(map(int, data[1:2 * n + 1]))
    del data

    starts = nums[0::2]
    ends = nums[1::2]
    del nums

    out = []
    app = out.append
    cur_s = cur_e = None

    # 先抽樣看起點是不是本來就遞增，是的話整個排序都能省掉
    step = max(1, n // 1000)
    presorted = all(starts[i] <= starts[i + step]
                    for i in range(0, n - step, step))
    if presorted:
        presorted = all(starts[i] <= starts[i + 1] for i in range(n - 1))

    if presorted:
        seq = zip(starts, ends)
    else:
        # 把 (s, e) 壓成一個整數再排序：排 int 比排 tuple 快一倍
        OFF = 1 << 40
        BIAS = 1 << 35          # 讓負數終點也能塞進低位
        MASK = OFF - 1
        keys = list(map(int.__add__,
                        map((OFF).__rmul__, starts),
                        map((BIAS).__add__, ends)))
        del starts, ends
        keys.sort()
        seq = ((k >> 40, (k & MASK) - BIAS) for k in keys)

    for s, e in seq:
        if cur_e is None:
            cur_s = s
            cur_e = e
        elif s > cur_e:
            # 沒重疊，收起目前這段，另開新的
            app("[%d,%d]" % (cur_s, cur_e))
            cur_s = s
            cur_e = e
        elif e > cur_e:
            # 有重疊，把終點往後拉
            cur_e = e

    app("[%d,%d]" % (cur_s, cur_e))
    sys.stdout.write("[" + ",".join(out) + "]\n")


main()
