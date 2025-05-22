#include <iostream>
using namespace std;
int main()
{
    int t, c;
    cin >> c >> t;
    for (int i = 0; i < t; i++)
    {
        int tt;
        cin >> tt;
        if (tt == c)
            cout << "correct" << endl;
        else
            cout << "wrong" << endl;
    }
}
