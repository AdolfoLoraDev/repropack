# Estimate pi via Monte Carlo and write it to results/pi.txt
mkpath("results")
n = 100_000
inside = 0
for _ in 1:n
    x, y = rand(), rand()
    global inside += (x^2 + y^2 <= 1) ? 1 : 0
end
open("results/pi.txt", "w") do io
    write(io, string(4 * inside / n))
end
println("Wrote results/pi.txt")
