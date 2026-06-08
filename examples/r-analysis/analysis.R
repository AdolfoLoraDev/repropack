# Compute summary statistics and write them to results/summary.txt
dir.create("results", showWarnings = FALSE)
x <- c(2, 4, 4, 4, 5, 5, 7, 9)
writeLines(
  c(paste("mean:", mean(x)), paste("sd:", sd(x))),
  "results/summary.txt"
)
cat("Wrote results/summary.txt\n")
