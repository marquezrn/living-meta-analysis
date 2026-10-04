# Install the exact dependency closure in lock-file order, without resolution.
options(timeout = 600, repos = c(CRAN = "https://cloud.r-project.org"))
stopifnot(getRversion() == "4.5.1")
arguments <- commandArgs(trailingOnly = TRUE)
if (length(arguments) != 1L) {
  stop("Provide the path to statistics/packages.lock.tsv")
}
packages <- read.delim(arguments[[1]], header = TRUE, sep = "\t",
                       colClasses = "character", stringsAsFactors = FALSE,
                       check.names = FALSE, comment.char = "#")
if (!identical(names(packages), c("package", "version")) || nrow(packages) < 1L ||
    anyNA(packages) || anyDuplicated(packages$package) ||
    any(!grepl("^[A-Za-z][A-Za-z0-9.]*$", packages$package)) ||
    any(!grepl("^[0-9][A-Za-z0-9.-]*$", packages$version))) {
  stop("Invalid R dependency lock")
}
core_pins <- c(jsonlite = "2.0.0", metafor = "4.8-0", clubSandwich = "0.6.1")
for (name in names(core_pins)) {
  matching <- packages$version[packages$package == name]
  if (length(matching) != 1L || matching != core_pins[[name]]) {
    stop(paste("Required scientific package pin differs:", name))
  }
}

install_exact <- function(name, version) {
  filename <- paste0(name, "_", version, ".tar.gz")
  urls <- c(paste0(getOption("repos")[["CRAN"]], "/src/contrib/", filename),
            paste0(getOption("repos")[["CRAN"]], "/src/contrib/Archive/", name, "/", filename))
  archive <- tempfile(fileext = ".tar.gz")
  on.exit(unlink(archive))
  for (url in urls) {
    downloaded <- tryCatch(download.file(url, archive, mode = "wb", quiet = TRUE) == 0,
                           error = function(error) FALSE)
    if (downloaded) {
      # Local archives and dependencies=FALSE prohibit fetching unpinned packages.
      withCallingHandlers(
        install.packages(archive, repos = NULL, type = "source", dependencies = FALSE),
        warning = function(warning) stop(conditionMessage(warning), call. = FALSE)
      )
      stopifnot(packageVersion(name) == package_version(version))
      message("Verified locked package: ", name, " ", version)
      return(invisible(TRUE))
    }
  }
  stop(paste("Pinned source unavailable:", name, version))
}

for (index in seq_len(nrow(packages))) {
  install_exact(packages$package[[index]], packages$version[[index]])
}
# Assert the entire closure again after installation, including transitive packages.
for (index in seq_len(nrow(packages))) {
  stopifnot(packageVersion(packages$package[[index]]) ==
              package_version(packages$version[[index]]))
}
