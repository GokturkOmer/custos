.PHONY: css css-watch

css:
	./tools/tailwindcss -i src/custos/analytics/dashboard/static/css/tailwind.src.css 	                    -o src/custos/analytics/dashboard/static/css/dist.css 	                    --minify

css-watch:
	./tools/tailwindcss -i src/custos/analytics/dashboard/static/css/tailwind.src.css 	                    -o src/custos/analytics/dashboard/static/css/dist.css 	                    --watch
