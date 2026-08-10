#include <bits/stdc++.h>
using namespace std;

int main() {
    cin.tie(0) -> sync_with_stdio(0);

    int n;
    cin>>n;

    multiset<long long> a;
    for (int i=0; i<n; ++i) {
        long long v;
        cin>>v;
        a.insert(v);
    }

    vector<long long> b(n);
    for (int i=0; i<n; i++) {
        cin>>b[i];
    }

    int w = 0;
    for (int i=0; i<n; i++) {
        auto it = a.upper_bound(b[i]);
        if (it == a.end()) {
            it = a.begin();
        } else {
            ++w;
        }
        a.erase(it);
    }

    cout<<w;
}