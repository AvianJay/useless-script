#include <iostream>
using namespace std;
int main()
{
    int n, no = 0, yes = 0;
    cin >> n;
    for (int i = 0; i < n; i++)
    {
        int inp;
        cin >> inp;
        yes += inp;
    }
    for (int i = 0; i < n; i++)
    {
        int inp;
        cin >> inp;
        no += inp;
    }
    cout << yes << " " << no << " " << yes - no;
}
