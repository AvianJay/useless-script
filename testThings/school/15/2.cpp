#include <iostream>
using namespace std;
int main()
{
    int t;
    cin >> t;
    int a[t] = {};
    for (int i = 0; i < t; i++)
    {
        int inp;
        cin >> inp;
        a[i] = inp;
    }
    for (int i = t - 1; i >= 0; i--)
        cout << a[i] << " ";
}