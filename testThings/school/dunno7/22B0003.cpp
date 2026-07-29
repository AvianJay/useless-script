#include <bits/stdc++.h>
using namespace std;

// n 最大 2e6，整份輸入一次讀進來自己解析，比 cin 快很多
static char buf[1 << 25];

int main() {
    size_t len = fread(buf, 1, sizeof(buf) - 1, stdin);
    buf[len] = 0;
    char *p = buf;

    auto readInt = [&]() -> int {
        while (*p && (*p < '0' || *p > '9')) p++;
        int v = 0;
        while (*p >= '0' && *p <= '9') { v = v * 10 + (*p - '0'); p++; }
        return v;
    };

    int n = readInt();
    int k = readInt();

    vector<int> a(n);
    for (int i = 0; i < n; i++) a[i] = readInt();

    // 只要第 k 大，不必整個排序：nth_element 平均 O(n)
    nth_element(a.begin(), a.begin() + (k - 1), a.end(), greater<int>());

    printf("%d\n", a[k - 1]);
    return 0;
}
