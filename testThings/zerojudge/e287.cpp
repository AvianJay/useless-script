#include<bits/stdc++.h>

using namespace std;

int main() {
    int n, m;
    cin >> n >> m;

    vector<vector<int>> nb(n, vector<int>(m));
    for (int i = 0; i < n; i++) {
        for (int j = 0; j < m; j++) {
            cin >> nb[i][j];
        }
    }

    // 優先佇列，存放 {值, 行, 列}
    priority_queue<tuple<int, int, int>, vector<tuple<int, int, int>>, greater<>> pq;

    // 初始化，找到最小值並加入佇列
    for (int i = 0; i < n; i++) {
        for (int j = 0; j < m; j++) {
            pq.emplace(nb[i][j], i, j);
        }
    }

    int ans = 0;
    vector<vector<bool>> visited(n, vector<bool>(m, false));
    vector<int> dx = {-1, 1, 0, 0}; // 上下左右
    vector<int> dy = {0, 0, -1, 1};

    while (!pq.empty()) {
        auto [value, x, y] = pq.top();
        pq.pop();

        if (visited[x][y]) continue; // 如果已經訪問過，跳過
        visited[x][y] = true;
        ans += value;

        // 檢查四個方向
        for (int d = 0; d < 4; d++) {
            int nx = x + dx[d];
            int ny = y + dy[d];

            if (nx >= 0 && nx < n && ny >= 0 && ny < m && !visited[nx][ny]) {
                pq.emplace(nb[nx][ny], nx, ny);
            }
        }
    }

    cout << ans << endl;
    return 0;
}