FROM rocker/r-ver:4.5.1
RUN R -q -e 'install.packages("remotes", repos="https://cloud.r-project.org"); remotes::install_version("jsonlite", "2.0.0", repos="https://cloud.r-project.org", upgrade="never"); remotes::install_version("metafor", "4.8-0", repos="https://cloud.r-project.org", upgrade="never"); remotes::install_version("clubSandwich", "0.6.1", repos="https://cloud.r-project.org", upgrade="never")'
COPY statistics/meta_analysis.R /app/meta_analysis.R
ENTRYPOINT ["Rscript", "--vanilla", "/app/meta_analysis.R"]
