#include <iostream>
using namespace std;
int main()
{
    float a;
    float b = 0;
    cin >> a;
    for (int i = 0; i < a; i++)
    {
        float c;
        cin >> c;
        b += c;
    }
    cout << b / a << endl;
    if (b / a >= 50)
    {
        cout << "Pass";
    }
    else
    {
        cout << "Fail";
    }
}
