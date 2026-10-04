FROM rocker/r-ver:4.5.1
COPY statistics/install_packages.R /tmp/install_packages.R
RUN Rscript --vanilla /tmp/install_packages.R
COPY statistics/meta_analysis.R /app/meta_analysis.R
ENTRYPOINT ["Rscript", "--vanilla", "/app/meta_analysis.R"]
