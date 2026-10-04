# Fixed JSON bridge. Neither model-generated expressions nor user formulas are evaluated.
suppressPackageStartupMessages(library(jsonlite))
input <- paste(readLines(file("stdin"), warn = FALSE), collapse = "\n")
request <- fromJSON(input, simplifyVector = TRUE)
for (pkg in names(request$packages)) {
  if (!requireNamespace(pkg, quietly = TRUE) || packageVersion(pkg) != package_version(request$packages[[pkg]])) {
    stop(paste("Required pinned package:", pkg, request$packages[[pkg]]))
  }
}
suppressPackageStartupMessages(library(metafor))
suppressPackageStartupMessages(library(clubSandwich))
data <- request$effects
if (request$dependent) {
  V <- as.matrix(request$covariance)
  fit <- rma.mv(yi, V = V, random = ~ 1 | study_family/id, method = "REML", data = data)
  test <- coef_test(fit, vcov = "CR2", cluster = data$study_family, test = "Satterthwaite")
  estimate <- as.numeric(test$beta[1])
  se <- as.numeric(test$SE[1])
  df <- as.numeric(test$df_Satt[1])
  if (!is.finite(df) || df <= 0 || !is.finite(se) || se <= 0) stop("CR2 inference is not estimable")
  bounds <- estimate + c(-1, 1) * qt(0.975, df) * se
  report <- list(estimate = estimate, se = se, ci_lower = bounds[1], ci_upper = bounds[2],
                 p_value = as.numeric(test$p_Satt[1]), robust_df = df,
                 heterogeneity_variances = as.numeric(fit$sigma2), inference = "REML + CR2 Satterthwaite",
                 k = fit$k, study_families = length(unique(data$study_family)))
} else {
  fit <- rma.uni(yi, vi, method = "REML", test = "adhoc", data = data)
  prediction <- predict(fit)
  report <- list(estimate = as.numeric(fit$b[1]), se = fit$se[1], ci_lower = fit$ci.lb[1],
                 ci_upper = fit$ci.ub[1], p_value = fit$pval[1], tau_squared = fit$tau2,
                 I_squared = fit$I2, Q = fit$QE, Q_p_value = fit$QEp,
                 prediction_lower = prediction$pi.lb, prediction_upper = prediction$pi.ub,
                 inference = "REML + safeguarded Knapp-Hartung", k = fit$k,
                 study_families = length(unique(data$study_family)))
}
if (request$dependent && report$robust_df < 4) {
  report$diagnostic_ci <- c(report$ci_lower, report$ci_upper)
  report$ci_lower <- NULL
  report$ci_upper <- NULL
  report$p_value <- NULL
  report$inference_available <- FALSE
} else {
  report$inference_available <- TRUE
}
if (request$measure == "ROM" && report$inference_available) {
  report$response_ratio <- exp(report$estimate)
  report$response_ratio_ci <- exp(c(report$ci_lower, report$ci_upper))
}
report$effect_measure <- request$measure
report$packages <- request$packages
report$R_version <- R.version.string
cat(toJSON(report, auto_unbox = TRUE, na = "null", digits = 12))
