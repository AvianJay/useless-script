#include<iostream>
using namespace std;
int main() {
    int t;
    cin>>t;
    for (int i=0; i<t; i++) {
        int result = 1;
        int n,k;
        cin>>n>>k;
        int a[n];
        for (int j=0; j<n; j++) {
            cin>>a[j];
        }
        for (int j=0; j<n; j++) {
            if (a[j] == 1 && a[j+1] == 1) {
                result = 0;
                break;
            }
        }
        if (result == 1) {
            cout << "true" << endl;
        } else {
            cout << "false" << endl;
        }
    }
}