#include <iostream>
using namespace std;
int main()
{
    int n;
    cin >> n;
    for (int i = 0; i < n; i++)
    {
        int a, b, c;
        bool o = false;
        cin >> a >> b >> c;
        for (int i = a; i <= b; i++)
        {
            if (i % c != 0)
            {
                cout << i << " ";
                o = true;
            }
        }
        if (!o)
            cout << "No spaces.";
        cout << endl;
    }
}
