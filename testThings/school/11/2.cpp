#include <iostream>
using namespace std;
int main()
{
    int m, n;
    cin >> m >> n;
    for (int i = m; i <= n; i++)
    {
        if (i % 9 != 0)
        {
            cout << i << " ";
        }
    }
}
