#include <iostream>
using namespace std;
int main()
{
    int a, b;
    int c = 0;
    cin >> a;
    cin >> b;
    for (int i = 1; i <= a & i <= b; i++)
    {
        // cout<<"DEBUG "<< a%i<<" "<<b%i<<" "<<i<<endl;
        if (a % i == 0 & b % i == 0)
        {
            c = i;
        }
    }
    cout << c;
}
