#include <iostream>
#include <sstream>
#include <climits> // For LLONG_MAX

int main() {
    long long a = 0; // Initialize 'a' with a starting value
    long long maxValue = 0; // Tracks the highest value reached
    int steps = 0; // Counts the number of steps
    std::ostringstream outputSequence;

    std::cout << "Enter a positive integer: ";
    std::cin >> a;

    if (a <= 0) {
        std::cerr << "Error: Input must be a positive integer." << std::endl;
        return 1;
    }

    outputSequence << a << " ";
    maxValue = a;

    while (a != 1) {
        if (a % 2 == 0) {
            a = a / 2;
        } else {
            a = a * 3 + 1;
        }
        steps++;
        if (a > maxValue) {
            maxValue = a;
        }
        if (a == LLONG_MAX) {
            std::cerr << "Error: Value exceeded LLONG_MAX." << std::endl;
            return 1;
        }
        outputSequence << a << " ";
    }

    std::cout << "Sequence of numbers: " << outputSequence.str() << std::endl;
    std::cout << "Maximum value: " << maxValue << std::endl;
    std::cout << "Total steps: " << steps << std::endl;

    return 0;
}
