#include <iostream>
using namespace std;
int main()
{
    int n, m, t = 0;
    cin >> n >> m;
    for (int i = n; i <= m; i++)
    {
        if (i % 3 == 0)
            t++;
    }
    cout << t;
}
