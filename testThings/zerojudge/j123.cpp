#include <iostream>
#include <vector>
#include <map>
using namespace std;

// 定義貨物形狀
vector<vector<pair<int, int>>> shapes = {
    {{0,0},{1,0},{2,0},{3,0}},           // A
    {{0,0},{0,1},{0,2},{0,3}},           // B
    {{0,0},{1,0},{0,1},{1,1}},           // C
    {{0,0},{1,0},{2,0},{2,1}},           // D
    {{0,1},{1,0},{1,1},{1,2}}            // E
};

int get_shape_index(char t) {
    return t - 'A';
}

int main() {
    int R, C, n;
    cin >> R >> C >> n;
    vector<vector<int>> warehouse(R, vector<int>(C, 0));
    int discard = 0;

    for (int i = 0; i < n; ++i) {
        char t;
        int d;
        cin >> t >> d;
        int idx = get_shape_index(t);
        auto &shape = shapes[idx];
        bool placed = false;
        for (int col = 0; col < C; ++col) {
            // 檢查是否超出右邊
            int max_col = 0, max_row = 0;
            for (auto &p : shape) {
                if (p.first > max_col) max_col = p.first;
                if (p.second > max_row) max_row = p.second;
            }
            if (col + max_col >= C) continue;
            if (d + max_row >= R) continue;
            // 檢查是否可放
            bool can_place = true;
            for (auto &p : shape) {
                int x = col + p.first;
                int y = d + p.second;
                if (warehouse[y][x]) {
                    can_place = false;
                    break;
                }
            }
            if (can_place) {
                for (auto &p : shape) {
                    int x = col + p.first;
                    int y = d + p.second;
                    warehouse[y][x] = 1;
                }
                placed = true;
                break;
            }
        }
        if (!placed) discard++;
    }

    // 計算剩餘空格
    int empty = 0;
    for (int i = 0; i < R; ++i)
        for (int j = 0; j < C; ++j)
            if (warehouse[i][j] == 0) empty++;

    cout << empty << " " << discard << endl;
    return 0;
}