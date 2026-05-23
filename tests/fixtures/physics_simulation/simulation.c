/*
 * Minimal C extension for projectile motion.
 * Compile with:
 *   gcc -shared -fPIC -o simulation.so simulation.c -lm
 */
#include <math.h>

/* Calculate the horizontal range of a projectile.
 *
 * v0    – initial velocity (m/s)
 * angle – launch angle in degrees
 * g     – gravitational acceleration (m/s^2)
 *
 * Returns range in meters.
 */
double projectile_range(double v0, double angle, double g) {
    double rad = angle * M_PI / 180.0;
    return (v0 * v0 * sin(2.0 * rad)) / g;
}
