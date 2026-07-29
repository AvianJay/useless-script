#include <bits/stdc++.h>
using namespace std;

int main()
{
    ios_base::sync_with_stdio(0);
    cin.tie(0);
    long long n;
    cin >> n;
    vector<pair<long long, long long>> ins;
    vector<pair<long long, long long>> me;
    for (long long i = 0; i < n; i++)
    {
        long long s, e;
        cin >> s >> e;
        ins.push_back({s, e});
    }
    sort(ins.begin(), ins.end());
    for (long long i = 0; i < n; i++) {
        if (!me.empty() && ins[i].first <= me.back().second) {
            if (ins[i].second > me.back().second)
                me.back().second = ins[i].second;
        } else {
            me.push_back(ins[i]);
        }
    }
    cout << "[";
    for (size_t i = 0; i < me.size(); i++) {
        if (i > 0)
            cout << ",";
        cout<<"["<<me[i].first<<","<<me[i].second<<"]";
    }
    cout << "]\n";
}
