#include <iostream>
using namespace std;
int main()
{
    int m, n, o;
    cin >> m >> n >> o;
    for (int i = m; i >= 0; i--)
    {
        if (m % i == 0 && n % i == 0 && o % i == 0)
        {
            cout << i;
            break;
        }
    }
}
