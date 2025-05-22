#include <iostream>
using namespace std;
int main()
{
    int n, m, o;
    cin >> n >> m >> o;
    for (int i = n; i <= m; i += o)
    {
        cout << i << " ";
    }
}
