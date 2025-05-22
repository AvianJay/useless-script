#include <iostream>
using namespace std;
int main()
{
    double t;
    double f = 0;
    cin >> t;
    for (int i = 0; i < t; i++)
    {
        double inp;
        cin >> inp;
        f += inp;
    }
    cout << f / t;
}