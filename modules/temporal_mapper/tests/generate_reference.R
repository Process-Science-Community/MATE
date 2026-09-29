args <- commandArgs(trailingOnly=TRUE)
if (length(args) != 1L) stop('Pass the GraphExplain TemporalMapperInteractive/R directory')
root <- args[[1L]]
source(file.path(root, 'TMapper_R/similarity.r'))
source(file.path(root, 'TMapper_R/cluster.r'))
source(file.path(root, 'TMapper_R/visual.r'))
source(file.path(root, 'TMapper_R/tmapper_pipeline.R'))
tmap <- function(...) NULL
tmap_color_interactive <- function(...) NULL
X <- matrix(c(3,1,0, 2,1,0, 0,0,4, 0,1,3, 3,1,0, 0,0,0, 0,0,0), ncol=3, byrow=TRUE)
dates <- as.Date(c('2024-01-01','2024-01-08','2024-01-22','2024-01-29','2024-02-05','2024-02-12','2024-02-19'))
frame <- data.frame(id=dates, X)
cases <- list()
for (method in c('hclust','heuristic')) for (mode in c('consecutive','adjacent')) for (d in c(1,2,3)) {
 r <- build_tmapper_pipeline(frame, cluster_method=method, temporal_edges=mode, d=d)
 cases[[length(cases)+1]] <- list(method=method, mode=mode, d=d, similarity=unname(r$cosine_sim), adjacency=unname(r$A), simplified=unname(r$simplified$A_simp), members=lapply(r$simplified$members, function(m) match(m,rownames(r$A))-1L))
}
jsonlite::write_json(list(matrix=X, periods=as.character(dates), cases=cases), 'modules/temporal_mapper/tests/r_reference.json', auto_unbox=TRUE, digits=16, pretty=TRUE)
