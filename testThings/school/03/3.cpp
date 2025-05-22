#include <iostream>
using namespace std;
int main()
{
    int a, b;
    cin >> a;
    cin >> b;
    for (int i = a; true; i++)
    {
        if (i % a == 0 & i % b == 0)
        {
            cout << i;
            break;
        }
    }
}
