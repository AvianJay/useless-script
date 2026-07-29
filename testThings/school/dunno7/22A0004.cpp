#include <bits/stdc++.h>
using namespace std;

int main() {
    ios::sync_with_stdio(false);
    cin.tie(nullptr);

    int n;
    if (!(cin >> n)) return 0;

    vector<pair<int, int>> p(n);
    for (int i = 0; i < n; i++) cin >> p[i].first >> p[i].second;

    // 照 x 排序，之後只要往回看 x 差距在目前最佳解以內的點
    sort(p.begin(), p.end());

    // 答案是最近距離的平方，座標到 1e4 所以最大 8e8，long long 保險
    long long best = LLONG_MAX;

    // 用 set 維護「x 差距夠近」的候選點，照 y 排序方便取區間
    set<pair<int, int>> box;   // (y, x)
    int left = 0;

    for (int i = 0; i < n; i++) {
        // best 是平方值，開根號得到實際距離門檻
        long long d = (long long)ceil(sqrt((double)best)) + 1;

        // 把 x 差距超過門檻的點移出候選
        while (left < i && (long long)p[i].first - p[left].first > d) {
            box.erase({p[left].second, p[left].first});
            left++;
        }

        // 只看 y 落在 [y-d, y+d] 的候選
        int y = p[i].second, x = p[i].first;
        auto lo = box.lower_bound({(int)max(-1000000LL, y - d), INT_MIN});
        auto hi = box.upper_bound({(int)min(1000000LL, y + d), INT_MAX});
        for (auto it = lo; it != hi; ++it) {
            long long dx = (long long)x - it->second;
            long long dy = (long long)y - it->first;
            long long cur = dx * dx + dy * dy;
            if (cur < best) best = cur;
        }

        box.insert({y, x});
    }

    // 半徑最大是最近距離的一半，平方後就是 best / 4
    // 用整數判斷避免浮點誤差：best/4 要嘛整數、.25、.5、.75
    long long q = best / 4, r = best % 4;
    if (r == 0) cout << q << "\n";
    else if (r == 1) cout << q << ".25\n";
    else if (r == 2) cout << q << ".5\n";
    else cout << q << ".75\n";

    return 0;
}
