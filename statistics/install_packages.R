# Install the specified scientific engine, with current-source/archive fallback.
options(timeout = 600, repos = c(CRAN = "https://cloud.r-project.org"))
stopifnot(getRversion() == "4.5.1")
install_bootstrap <- function(name, version) {
  filename <- paste0(name, "_", version, ".tar.gz")
  urls <- c(paste0(getOption("repos")[["CRAN"]], "/src/contrib/", filename),
            paste0(getOption("repos")[["CRAN"]], "/src/contrib/Archive/", name, "/", filename))
  archive <- tempfile(fileext = ".tar.gz")
  on.exit(unlink(archive))
  for (url in urls) {
    downloaded <- tryCatch(download.file(url, archive, mode = "wb", quiet = TRUE) == 0,
                           error = function(error) FALSE)
    if (downloaded) {
      install.packages(archive, repos = NULL, type = "source")
      stopifnot(packageVersion(name) == package_version(version))
      return(invisible(TRUE))
    }
  }
  stop(paste("Pinned source unavailable:", name, version))
}
install_bootstrap("remotes", "2.5.0")
packages <- c(jsonlite = "2.0.0", metafor = "4.8-0", clubSandwich = "0.6.1")
for (name in names(packages)) {
  remotes::install_version(name, packages[[name]], dependencies = NA, upgrade = "never")
  stopifnot(packageVersion(name) == package_version(packages[[name]]))
}
